"""市场状态分类(spec §8.4)。

M1-M5 打分,映射 ATTACK/NEUTRAL/DEFENSE。
宽度数据不可用时降级(degraded=True,新开仓风险减半)。
"""
from dataclasses import dataclass

from ..domain import DailyBar
from ..services.indicator_service import sma, realized_volatility


# spec §8.2 regime 仓位上限
_REGIME_EXPOSURE = {"ATTACK": 0.60, "NEUTRAL": 0.40, "DEFENSE": 0.20}


@dataclass(frozen=True)
class MarketRegimeResult:
    score: int
    regime: str               # ATTACK / NEUTRAL / DEFENSE
    degraded: bool
    recommended_exposure: float
    m_signals: dict           # {"M1": 1, "M2": 1, ...} 便于审计


def classify_regime(score: int) -> MarketRegimeResult:
    """score → regime + 建议仓位(spec §8.4 表格)。"""
    if score >= 2:
        regime = "ATTACK"
    elif score >= 0:  # 0 或 1
        regime = "NEUTRAL"
    else:  # <= -1
        regime = "DEFENSE"
    return MarketRegimeResult(
        score=score, regime=regime, degraded=False,
        recommended_exposure=_REGIME_EXPOSURE[regime], m_signals={},
    )


def compute_market_score(*, benchmark_bars: list[DailyBar],
                         breadth_ratio: float | None,
                         volatility_percentile: float) -> MarketRegimeResult:
    """计算市场状态分数(spec §8.4)。

    benchmark_bars: 基准指数 K 线(升序,调用方负责防未来函数)。
    breadth_ratio: 全市场 close>MA20 比例(None 表示降级)。
    volatility_percentile: 基准 20 日实现波动率在近 252 日的分位(0-1)。
    """
    if not benchmark_bars:
        raise ValueError("基准 K 线不能为空")
    closes = [b.close for b in benchmark_bars]
    last_close = closes[-1]
    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60)
    degraded = breadth_ratio is None

    signals = {}
    # M1: 收盘 > MA20
    m1 = 1 if (ma20 is not None and last_close > ma20) else 0
    signals["M1"] = m1
    # M2: MA20 > MA60
    m2 = 1 if (ma20 is not None and ma60 is not None and ma20 > ma60) else 0
    signals["M2"] = m2
    # M3: 宽度 >= 55%
    if breadth_ratio is not None:
        m3 = 1 if breadth_ratio >= 0.55 else 0
    else:
        m3 = 0  # 降级时不贡献
    signals["M3"] = m3
    # M4: 波动率在 80% 分位以上 -> -1
    m4 = -1 if volatility_percentile >= 0.80 else 0
    signals["M4"] = m4
    # M5: 收盘 < MA60 -> -1
    m5 = -1 if (ma60 is not None and last_close < ma60) else 0
    signals["M5"] = m5

    score = m1 + m2 + m3 + m4 + m5
    base = classify_regime(score)
    return MarketRegimeResult(
        score=score, regime=base.regime, degraded=degraded,
        recommended_exposure=base.recommended_exposure, m_signals=signals,
    )
