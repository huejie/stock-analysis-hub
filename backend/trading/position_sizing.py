"""仓位计算与风险预算纯函数(spec §8.2/8.7/8.8)。

全部无副作用,Phase 3 注入市场状态/ATR/入场价等参数。
A 股规则:100 股整数倍,T+1。
"""
from dataclasses import dataclass


# spec §8.2 默认阈值(可被调用方覆盖)
CONSECUTIVE_LOSS_THRESHOLD = 3
CONSECUTIVE_LOSS_REDUCED_RISK = 0.0025  # 0.25%
MIN_RISK_FLOOR = 0.001  # 0.1%
MIN_STOP_DISTANCE = 0.03  # 3%
MAX_STOP_DISTANCE = 0.10  # 10%


@dataclass(frozen=True)
class StopPriceResult:
    """止损价计算结果。"""
    stop_price: float | None       # None 表示无效(距离超 10%)
    stop_distance_pct: float
    is_valid: bool


@dataclass(frozen=True)
class BuyQuantityResult:
    """买入数量计算结果。"""
    buy_quantity: int              # 0 表示不可交易
    risk_quantity: int             # 按风险预算算出的数量
    cap_quantity: int              # 按仓位上限算出的数量
    cash_quantity: int             # 按现金算出的数量
    risk_amount: float             # 预期最大损失金额 = buy_quantity * (entry-stop)
    risk_budget: float             # 可用风险预算 = equity * effective_risk
    capped_by: str                 # "risk" / "position_limit" / "cash" / "none" / "no_trade"
    is_tradable: bool


def compute_stop_price(*, entry_price: float, raw_stop: float) -> StopPriceResult:
    """计算止损价(spec §8.7)。

    raw_stop 来自 max(近10日最低价, entry - 2*ATR14)。
    距离 < 3% 扩展为 entry 下方 3%;距离 > 10% 无效。
    """
    distance = (entry_price - raw_stop) / entry_price
    if distance > MAX_STOP_DISTANCE:
        return StopPriceResult(stop_price=None, stop_distance_pct=distance, is_valid=False)
    if distance < MIN_STOP_DISTANCE:
        # 扩展为 entry 下方 3%
        stop = entry_price * (1 - MIN_STOP_DISTANCE)
        return StopPriceResult(stop_price=stop, stop_distance_pct=MIN_STOP_DISTANCE, is_valid=True)
    return StopPriceResult(stop_price=raw_stop, stop_distance_pct=distance, is_valid=True)


def effective_risk_per_trade(
    *, base_risk: float, consecutive_losses: int,
    regime: str, degraded: bool,
    max_risk: float | None = None,
) -> float:
    """计算有效单笔风险比例(spec §8.2/8.4/8.7)。

    规则叠加(乘法):
    - 连续亏损 >= 3 笔:用 0.25% 替代 base(取较小)
    - 中性市场:减半
    - 数据降级:减半
    最后 clamp 到 [0.1%, max_risk 或 base_risk]。
    """
    # 连续亏损:降至固定值(取与 base 的较小)
    if consecutive_losses >= CONSECUTIVE_LOSS_THRESHOLD:
        effective = min(base_risk, CONSECUTIVE_LOSS_REDUCED_RISK)
    else:
        effective = base_risk
    # 中性市场减半
    if regime == "NEUTRAL":
        effective *= 0.5
    # 数据降级减半
    if degraded:
        effective *= 0.5
    # 防守市场:Phase 3 不开新仓,这里不处理(由调用方判断)
    # clamp 下限
    effective = max(effective, MIN_RISK_FLOOR)
    # clamp 上限
    ceiling = max_risk if max_risk is not None else base_risk
    effective = min(effective, ceiling)
    return effective


def compute_buy_quantity(
    *, total_equity: float, cash: float,
    entry_price: float, stop_price: float,
    effective_risk_per_trade: float,
    max_single_position: float,
) -> BuyQuantityResult:
    """计算建议买入数量(spec §8.7)。

    buy = min(risk_quantity, cap_quantity, cash_quantity),按 100 股向下取整。
    < 100 股不可交易。
    """
    risk_budget = total_equity * effective_risk_per_trade
    per_share_risk = entry_price - stop_price
    if per_share_risk <= 0:
        return BuyQuantityResult(
            buy_quantity=0, risk_quantity=0, cap_quantity=0, cash_quantity=0,
            risk_amount=0, risk_budget=risk_budget, capped_by="no_trade", is_tradable=False,
        )

    # risk_quantity: floor(budget/per_share/100)*100
    risk_quantity = (int(risk_budget / per_share_risk) // 100) * 100
    # cap_quantity: floor(equity*cap_pct/entry/100)*100
    cap_quantity = (int(total_equity * max_single_position / entry_price) // 100) * 100
    # cash_quantity: floor(cash/entry/100)*100
    cash_quantity = (int(cash / entry_price) // 100) * 100

    buy = min(risk_quantity, cap_quantity, cash_quantity)

    if buy < 100:
        return BuyQuantityResult(
            buy_quantity=0, risk_quantity=risk_quantity, cap_quantity=cap_quantity,
            cash_quantity=cash_quantity, risk_amount=0, risk_budget=risk_budget,
            capped_by="no_trade", is_tradable=False,
        )

    # 判定被谁限制
    if buy == cash_quantity and cash_quantity <= risk_quantity and cash_quantity <= cap_quantity:
        capped_by = "cash"
    elif buy == cap_quantity and cap_quantity <= risk_quantity:
        capped_by = "position_limit"
    else:
        capped_by = "risk"

    risk_amount = buy * per_share_risk
    return BuyQuantityResult(
        buy_quantity=buy, risk_quantity=risk_quantity, cap_quantity=cap_quantity,
        cash_quantity=cash_quantity, risk_amount=risk_amount, risk_budget=risk_budget,
        capped_by=capped_by, is_tradable=True,
    )
