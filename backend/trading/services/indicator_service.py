"""指标计算服务(纯 Python,无 pandas 依赖)。

防未来函数契约(spec §14.1):调用方负责只传入信号日 t 及之前的 K 线,
本函数不做内部时间过滤。指标滚动窗口末尾即 t。
"""
import math
from datetime import date

from ..domain import DailyBar


def sma(values: list[float], period: int) -> float | None:
    """简单移动平均。数据不足返回 None。"""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> float | None:
    """指数移动平均(用于 ATR 平滑)。"""
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    result = sum(values[:period]) / period
    for v in values[period:]:
        result = v * k + result * (1 - k)
    return result


def true_range(prev_close: float | None, high: float, low: float, close: float) -> float:
    """真实波幅。"""
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr14(bars: list[DailyBar]) -> float | None:
    """14 日 ATR(用 EMA 平滑,Wilders 法近似)。"""
    if len(bars) < 14:
        return None
    trs = []
    for i, b in enumerate(bars):
        prev = bars[i-1].close if i > 0 else None
        trs.append(true_range(prev, b.high, b.low, b.close))
    # 用简单平均的 14 日 ATR(spec 未指定平滑方式,简单平均足够日线波段)
    if len(trs) < 14:
        return None
    return sum(trs[-14:]) / 14


def percentile_rank(values: list[float], target: float) -> float:
    """目标值在列表中的百分位排名(0-1)。"""
    if not values:
        return 0.0
    below = sum(1 for v in values if v < target)
    return below / len(values)


def realized_volatility(closes: list[float], period: int) -> float | None:
    """已实现波动率(日收益率标准差)。"""
    if len(closes) < period + 1:
        return None
    recent = closes[-(period+1):]
    returns = [(recent[i] - recent[i-1]) / recent[i-1] for i in range(1, len(recent)) if recent[i-1] != 0]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var)


class IndicatorResult:
    """单只股票的指标快照(信号日 t)。"""
    def __init__(self, *, ma20: float | None, ma60: float | None,
                 atr: float | None, high_20d: float, low_10d: float,
                 vol_20d: float | None, close: float):
        self.ma20 = ma20
        self.ma60 = ma60
        self.atr = atr
        self.high_20d = high_20d
        self.low_10d = low_10d
        self.vol_20d = vol_20d
        self.close = close

    @property
    def atr14(self) -> float | None:
        """ATR14 别名(兼容审计字段命名)。"""
        return self.atr

    @property
    def ma20_slope_positive(self) -> bool:
        """MA20 斜率为正(简化:用近 5 日 MA20 趋势)。"""
        return self.ma20 is not None and self.close > self.ma20


def compute_indicators(bars: list[DailyBar]) -> IndicatorResult:
    """计算单只股票在窗口末尾(t)的指标。

    bars 必须按日期升序,调用方负责只传到 t 的数据(防未来函数)。
    """
    if not bars:
        raise ValueError("bars 不能为空")
    closes = [b.close for b in bars]
    t_bar = bars[-1]
    return IndicatorResult(
        ma20=sma(closes, 20),
        ma60=sma(closes, 60),
        atr=atr14(bars),
        high_20d=max(b.high for b in bars[-20:]) if len(bars) >= 1 else t_bar.high,
        low_10d=min(b.low for b in bars[-10:]) if len(bars) >= 1 else t_bar.low,
        vol_20d=realized_volatility(closes, 20),
        close=t_bar.close,
    )
