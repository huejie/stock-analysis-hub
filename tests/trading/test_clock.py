from datetime import date
from backend.trading.clock import is_weekend, target_trade_date_after


def test_is_weekend_saturday():
    assert is_weekend(date(2026, 7, 25)) is True  # 周六


def test_is_weekend_monday():
    assert is_weekend(date(2026, 7, 20)) is False  # 周一


def test_target_trade_date_after_skips_weekend():
    # 2026-07-24 是周五,t+1 跳到下周一 2026-07-27
    assert target_trade_date_after(date(2026, 7, 24)) == date(2026, 7, 27)


def test_target_trade_date_after_weekday():
    # 2026-07-21 是周二,t+1 是周三 2026-07-22
    assert target_trade_date_after(date(2026, 7, 21)) == date(2026, 7, 22)
