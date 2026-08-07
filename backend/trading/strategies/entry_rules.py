"""入场规则(spec §8.6)。

6 条件全部满足才 CONDITIONAL_BUY,否则 WATCH/FORBIDDEN。
触发价/追高价计算。
"""
from dataclasses import dataclass

from ..position_sizing import compute_stop_price, StopPriceResult
from ..services.indicator_service import IndicatorResult
from .scoring import ScoreBreakdown


TICK = 0.01  # A 股最小价格变动(spec §8.6 未给值,简化)


@dataclass(frozen=True)
class TriggerPrices:
    trigger_price: float
    do_not_chase_price: float
    breakout_level: float


@dataclass(frozen=True)
class EntryResult:
    action: str                    # CONDITIONAL_BUY / WATCH / FORBIDDEN
    stop_price: float | None
    stop_distance_pct: float | None
    trigger_prices: TriggerPrices | None
    target_2r_price: float | None
    rule_hits: list
    rule_misses: list
    invalidation_reason: str | None


def compute_trigger_prices(*, breakout_level: float, signal_day_high: float,
                           tick: float = TICK) -> TriggerPrices:
    """计算触发价/追高价(spec §8.6)。"""
    trigger = max(breakout_level, signal_day_high) + tick
    return TriggerPrices(
        trigger_price=trigger,
        do_not_chase_price=trigger * 1.03,
        breakout_level=breakout_level,
    )


def evaluate_entry(*, regime: str, score: ScoreBreakdown, ind: IndicatorResult,
                   entry_price: float, atr: float, low_10d: float,
                   portfolio_has_capacity: bool, min_score: int = 70,
                   instrument_status=None) -> EntryResult:
    """评估入场条件(spec §8.3 硬过滤 + §8.6 6 条件)。

    instrument_status: InstrumentStatus 域对象(可选)。None 时跳过硬过滤
    (Provider 暂为 stub,Phase 5 后续接入真实数据)。
    """
    hits, misses = list(score.rule_hits), []

    # ---- spec §8.3 硬过滤(禁止交易)----
    if instrument_status is not None:
        if instrument_status.is_st or instrument_status.is_delisting:
            misses.append("ST_OR_DELISTING")
            return EntryResult("FORBIDDEN", None, None, None, None, hits, misses,
                              "ST/退市标的,禁止交易")
        if instrument_status.is_suspended:
            misses.append("SUSPENDED")
            return EntryResult("FORBIDDEN", None, None, None, None, hits, misses,
                              "停牌标的,禁止交易")
        # 一字涨跌停(开盘=最高=最低=收盘,且触限价)
        if (instrument_status.limit_up_price is not None
                and ind.close >= instrument_status.limit_up_price
                and ind.high == ind.low):
            misses.append("LIMIT_UP_ONE_PRICE")
            return EntryResult("FORBIDDEN", None, None, None, None, hits, misses,
                              "一字涨停,无法成交")
        if (instrument_status.limit_down_price is not None
                and ind.close <= instrument_status.limit_down_price
                and ind.high == ind.low):
            misses.append("LIMIT_DOWN_ONE_PRICE")
            return EntryResult("FORBIDDEN", None, None, None, None, hits, misses,
                              "一字跌停,无法成交")

    # 条件 1: 市场非 DEFENSE
    if regime == "DEFENSE":
        misses.append("DEFENSE_MARKET")
        return EntryResult("WATCH", None, None, None, None, hits, misses, "防守市场不开新仓")

    # 条件 5: 组合额度(提前判,避免无意义计算)
    if not portfolio_has_capacity:
        misses.append("NO_PORTFOLIO_CAPACITY")
        return EntryResult("FORBIDDEN", None, None, None, None, hits, misses, "组合无剩余仓位/数量/行业额度")

    # 条件 2: 评分 >= min_score
    if score.total < min_score:
        misses.append(f"SCORE_BELOW_{min_score}")
        return EntryResult("WATCH", None, None, None, None, hits, misses,
                          f"评分 {score.total} 低于门槛 {min_score}")

    # 条件 6: 止损距离 3%-10%
    raw_stop = max(low_10d, entry_price - 2 * atr)
    stop_result = compute_stop_price(entry_price=entry_price, raw_stop=raw_stop)
    if not stop_result.is_valid:
        misses.append("STOP_DISTANCE_INVALID")
        return EntryResult("WATCH", None, stop_result.stop_distance_pct, None, None,
                          hits, misses, f"止损距离 {stop_result.stop_distance_pct:.2%} 超出 3%-10%")

    # 条件 3: 近 20 日高点(距高点 <= 2% 或有效突破)
    # 已在评分中体现(NEAR_20D_HIGH),这里用评分 hit 判断
    if "NEAR_20D_HIGH" not in score.rule_hits:
        # 不在近高点,检查是否刚突破(收盘 > 前 20 日高点)
        if ind.close <= ind.high_20d:
            misses.append("NOT_NEAR_HIGH")
            return EntryResult("WATCH", stop_result.stop_price, stop_result.stop_distance_pct,
                              None, None, hits, misses, "未处于近 20 日高点 2% 内且未突破")

    # 全部条件满足
    hits.append("ENTRY_CONDITIONAL_BUY")
    trigger = compute_trigger_prices(
        breakout_level=ind.high_20d, signal_day_high=ind.close * 1.01,  # 信号日高近似
    )
    target_2r = entry_price + 2 * (entry_price - stop_result.stop_price)
    return EntryResult(
        action="CONDITIONAL_BUY",
        stop_price=stop_result.stop_price,
        stop_distance_pct=stop_result.stop_distance_pct,
        trigger_prices=trigger,
        target_2r_price=target_2r,
        rule_hits=hits, rule_misses=misses,
        invalidation_reason=f"开盘或成交价高于 {trigger.do_not_chase_price:.2f} 元则取消",
    )
