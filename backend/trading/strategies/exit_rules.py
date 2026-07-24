"""退出规则(spec §8.8)。

6 级优先:风险事件 > 初始止损 > 移动止损 > 趋势失效 > 分批止盈 > 再平衡。
移动止损只上移不下移。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ExitResult:
    action: str          # EXIT / REDUCE / HOLD
    reason: str          # INITIAL_STOP / TRAILING_STOP / TREND_FAILURE / BELOW_MA60 / TARGET_2R / HOLD / FORCE_EXIT
    new_trailing_stop: float | None
    detail: str = ""


def compute_trailing_stop(*, prev_trailing: float | None, highest_close: float,
                          atr: float) -> float:
    """计算移动止损(spec §8.8):max(上一日, 最高收盘 - 2.5*ATR)。只上移。"""
    candidate = highest_close - 2.5 * atr
    if prev_trailing is not None:
        return max(prev_trailing, candidate)
    return candidate


def evaluate_exit(*, close: float, initial_stop: float | None,
                  trailing_stop: float | None,
                  ma20: float | None, ma60: float | None,
                  prev_close: float, prev_prev_close: float,
                  entry_price: float, atr: float, highest_close: float,
                  target_2r_price: float | None = None,
                  force_exit: bool = False) -> ExitResult:
    """评估持仓退出(spec §8.8 6 级优先)。

    返回 EXIT(立即退出)/REDUCE(减仓)/HOLD(持有)。
    """
    # 优先级 1: 强制退出
    if force_exit:
        return ExitResult("EXIT", "FORCE_EXIT", trailing_stop, "人工强制退出或风险事件")

    # 优先级 2: 初始止损
    if initial_stop is not None and close <= initial_stop:
        return ExitResult("EXIT", "INITIAL_STOP", trailing_stop,
                         f"收盘 {close} 触及初始止损 {initial_stop}")

    # 优先级 3: 移动止损(先算新的,再判断是否触及旧的)
    new_trailing = compute_trailing_stop(
        prev_trailing=trailing_stop, highest_close=highest_close, atr=atr
    )
    if trailing_stop is not None and close <= trailing_stop:
        return ExitResult("EXIT", "TRAILING_STOP", new_trailing,
                         f"收盘 {close} 触及移动止损 {trailing_stop}")

    # 优先级 4: 趋势失效(连续 2 日 < MA20,或单日 < MA60)
    if ma60 is not None and close < ma60:
        return ExitResult("EXIT", "BELOW_MA60", new_trailing,
                         f"收盘 {close} 低于 MA60 {ma60}")
    if ma20 is not None and close < ma20 and prev_close < ma20:
        return ExitResult("EXIT", "TREND_FAILURE", new_trailing,
                         "连续 2 日收盘低于 MA20")

    # 优先级 5: +2R 分批止盈
    if target_2r_price is not None and close >= target_2r_price:
        return ExitResult("REDUCE", "TARGET_2R", new_trailing,
                         f"达到 +2R 目标 {target_2r_price},建议卖出 1/3")

    # 持有
    return ExitResult("HOLD", "HOLD", new_trailing, "持仓规则未触发退出")
