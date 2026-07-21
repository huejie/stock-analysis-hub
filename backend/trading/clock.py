"""交易日与时区抽象。

Phase 1 只实现基于周末的交易日判断;节假日日历(从 Provider 获取)在
market_data_service 中合并使用。固定时区 Asia/Shanghai。
"""
from datetime import date, timedelta

TIMEZONE = "Asia/Shanghai"


def is_weekend(d: date) -> bool:
    """周六/周日返回 True。"""
    return d.weekday() >= 5


def target_trade_date_after(signal_date: date) -> date:
    """信号日的下一个交易日(仅跳过周末,节假日由调用方叠加判断)。

    用于"收盘后生成下一交易日计划":signal_date 是 t,target 是 t+1 的最近交易日。
    """
    candidate = signal_date + timedelta(days=1)
    while is_weekend(candidate):
        candidate += timedelta(days=1)
    return candidate
