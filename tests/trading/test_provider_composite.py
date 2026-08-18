from datetime import date
import pytest

from backend.trading.providers.composite import CompositeProvider
from backend.trading.providers.base import ProviderError, ProviderUnavailable
from backend.trading.providers.eastmoney import EastmoneyProvider
from backend.trading.domain import DailyBar, TradeDay


def _make_bar(code="000001.SZ"):
    return DailyBar(code=code, trade_date=date(2026, 7, 20),
                    open=10.0, high=10.5, low=9.8, close=10.2, volume=1000, source="x")


class FakeProvider:
    def __init__(self, name, bars=None, error=None):
        self.name = name
        self._bars = bars
        self._error = error
        self.call_count = 0

    def get_daily_bars(self, codes, start, end):
        self.call_count += 1
        if self._error:
            raise self._error
        return list(self._bars or [])


def test_composite_uses_primary_on_success():
    primary = FakeProvider("eastmoney", bars=[_make_bar()])
    fallback = FakeProvider("akshare", bars=[_make_bar()])
    cp = CompositeProvider([primary, fallback])
    bars = cp.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20))
    assert len(bars) == 1
    assert primary.call_count == 1
    assert fallback.call_count == 0


def test_composite_falls_back_on_primary_error():
    primary = FakeProvider("eastmoney", error=ProviderError("eastmoney", "boom", retriable=True))
    fallback = FakeProvider("akshare", bars=[_make_bar()])
    # max_retries=1:隔离 failover 行为,不与重试逻辑耦合
    cp = CompositeProvider([primary, fallback], max_retries=1, retry_base_delay=0)
    bars = cp.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20))
    assert len(bars) == 1
    assert primary.call_count == 1
    assert fallback.call_count == 1


def test_composite_all_fail_raises_unavailable():
    primary = FakeProvider("eastmoney", error=ProviderError("eastmoney", "down", retriable=True))
    fallback = FakeProvider("akshare", error=ProviderError("akshare", "down", retriable=True))
    cp = CompositeProvider([primary, fallback])
    with pytest.raises(ProviderUnavailable):
        cp.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20))


def test_composite_empty_result_does_not_failover():
    """主源成功但返回空(股票无数据),不应切换备用源。"""
    primary = FakeProvider("eastmoney", bars=[])
    fallback = FakeProvider("akshare", bars=[_make_bar()])
    cp = CompositeProvider([primary, fallback])
    bars = cp.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20))
    assert bars == []
    assert primary.call_count == 1
    assert fallback.call_count == 0


def test_composite_retry_on_retriable_error():
    """retriable 错误应重试指定次数后才切换备用源。"""
    primary = FakeProvider("eastmoney",
                           error=ProviderError("eastmoney", "timeout", retriable=True))
    fallback = FakeProvider("akshare", bars=[_make_bar()])
    cp = CompositeProvider([primary, fallback], max_retries=3, retry_base_delay=0)
    cp.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20))
    # 主源重试 3 次后切换
    assert primary.call_count == 3


def test_composite_non_retriable_skips_retry():
    """不可重试错误(如 akshare 未安装)应立即切换,不重试。"""
    primary = FakeProvider("eastmoney",
                           error=ProviderError("eastmoney", "not installed", retriable=False))
    fallback = FakeProvider("akshare", bars=[_make_bar()])
    cp = CompositeProvider([primary, fallback], max_retries=3, retry_base_delay=0)
    cp.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20))
    assert primary.call_count == 1  # 不重试
    assert fallback.call_count == 1


def test_composite_falls_back_on_tencent_protocol_error(monkeypatch):
    primary = EastmoneyProvider()
    monkeypatch.setattr(
        primary,
        "_http_get",
        lambda *args, **kwargs: {
            "code": 0,
            "msg": "param error",
            "data": [],
        },
    )
    fallback = FakeProvider("akshare", bars=[_make_bar()])
    cp = CompositeProvider(
        [primary, fallback], max_retries=1, retry_base_delay=0
    )

    bars = cp.get_daily_bars(
        ["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20)
    )

    assert len(bars) == 1
    assert fallback.call_count == 1


def test_calendar_failure_does_not_circuit_daily_bars():
    class CalendarUnsupportedProvider(FakeProvider):
        def get_trade_calendar(self, start, end):
            raise ProviderError(
                self.name, "no trusted calendar", retriable=False
            )

    class CalendarFallbackProvider(FakeProvider):
        def get_trade_calendar(self, start, end):
            return []

    primary = CalendarUnsupportedProvider("eastmoney", bars=[_make_bar()])
    fallback = CalendarFallbackProvider("akshare", bars=[_make_bar()])
    cp = CompositeProvider([primary, fallback], max_retries=1, retry_base_delay=0)

    cp.get_trade_calendar(date(2026, 10, 1), date(2026, 10, 1))
    bars = cp.get_daily_bars(
        ["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20)
    )

    assert len(bars) == 1
    assert primary.call_count == 1
    assert fallback.call_count == 0


def test_calendar_with_source_uses_fallback_provider_name():
    class BrokenCalendarProvider(FakeProvider):
        def get_trade_calendar(self, start, end):
            self.call_count += 1
            raise ProviderError(self.name, "calendar down", retriable=True)

    class CalendarProvider(FakeProvider):
        def get_trade_calendar(self, start, end):
            self.call_count += 1
            return [
                TradeDay(date(2026, 10, 1), False),
                TradeDay(date(2026, 10, 2), False),
            ]

    primary = BrokenCalendarProvider("eastmoney")
    fallback = CalendarProvider("akshare")
    cp = CompositeProvider([primary, fallback], max_retries=1, retry_base_delay=0)

    days, source = cp.get_trade_calendar_with_source(
        date(2026, 10, 1), date(2026, 10, 2)
    )

    assert len(days) == 2
    assert source == "akshare"
    assert primary.call_count == 1
    assert fallback.call_count == 1
