"""股票评分(spec §8.5)。

5 维度合计 100 + 辅助加分上限 10(总分封顶 100)。
所有输入只能来自信号日收盘前可知数据。
"""
from dataclasses import dataclass, field

from ..services.indicator_service import IndicatorResult


@dataclass(frozen=True)
class ScoreBreakdown:
    trend: int               # 中期趋势 /30
    momentum: int            # 短期动量 /20
    volume_structure: int    # 量价结构 /20
    volatility: int          # 波动风险 /15
    relative_strength: int   # 相对强弱 /15
    auxiliary: int           # 辅助加分 /10
    rule_hits: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return min(100, self.trend + self.momentum + self.volume_structure
                   + self.volatility + self.relative_strength + self.auxiliary)


def compute_score(ind: IndicatorResult, *, pool_return_percentile: float,
                  excess_20d: float, excess_60d: float,
                  volume_ratio: float, has_gap: bool,
                  auxiliary_bonus: int) -> ScoreBreakdown:
    """计算股票评分(spec §8.5)。

    pool_return_percentile: 股票池内 20 日收益分位(0-1)。
    excess_20d/60d: 相对基准的超额收益(正为强)。
    volume_ratio: 量比。
    has_gap: 近 20 日是否有异常跳空/一字板。
    auxiliary_bonus: 辅助信号加分(调用方计算,上限 10)。
    """
    hits = []

    # 中期趋势 30
    trend = 0
    if ind.ma20 is not None and ind.close > ind.ma20:
        trend += 10; hits.append("TREND_ABOVE_MA20")
    if ind.ma20 is not None and ind.ma60 is not None and ind.ma20 > ind.ma60:
        trend += 10; hits.append("MA20_ABOVE_MA60")
    if ind.ma20_slope_positive:
        trend += 10; hits.append("MA60_SLOPE_POS")
    trend = min(30, trend)

    # 短期动量 20:分位数 0-1 映射 0-20 分
    momentum = int(pool_return_percentile * 20)
    momentum = max(0, min(20, momentum))
    if momentum > 0:
        hits.append("MOMENTUM_POS")

    # 量价结构 20
    vs = 0
    # 收盘距前 20 日高点 <= 2%
    if ind.high_20d > 0 and (ind.high_20d - ind.close) / ind.high_20d <= 0.02:
        vs += 10; hits.append("NEAR_20D_HIGH")
    # 量比 1.0-2.5
    if 1.0 <= volume_ratio <= 2.5:
        vs += 10; hits.append("VOLUME_RATIO_OK")
    vs = min(20, vs)

    # 波动风险 15
    vol = 0
    if ind.atr is not None and ind.close > 0:
        atr_pct = ind.atr / ind.close
        if 0.02 <= atr_pct <= 0.06:
            vol += 10; hits.append("ATR_IN_RANGE")
    if not has_gap:
        vol += 5; hits.append("NO_GAP")
    vol = min(15, vol)

    # 相对强弱 15
    rs = 0
    if excess_20d > 0:
        rs += 8; hits.append("RS_20D_POS")
    if excess_60d > 0:
        rs += 7; hits.append("RS_60D_POS")
    rs = min(15, rs)

    # 辅助加分(上限 10)
    aux = min(10, max(0, auxiliary_bonus))

    return ScoreBreakdown(
        trend=trend, momentum=momentum, volume_structure=vs,
        volatility=vol, relative_strength=rs, auxiliary=aux, rule_hits=hits,
    )
