import os
from datetime import date
import pytest

from backend.trading.services.market_data_service import MarketDataService
from backend.trading.repository import TradingRepository
from backend.trading.migrations import run_migrations
from backend.trading.domain import DailyBar, TradeDay
from backend.trading.errors import (
    CalendarUnavailableError,
    EmptyProviderResultError,
    ProviderUnavailableError,
)
from backend.trading.providers.base import ProviderError, ProviderUnavailable
from backend.trading.providers.composite import CompositeProvider

TEST_DB = "data/test_mds.db"


@pytest.fixture
def setup():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    repo = TradingRepository(TEST_DB)
    # 预置股票池
    repo.create_stock_pool_version("default", [
        {"stock_code": "000001.SZ", "stock_name": "平安"},
        {"stock_code": "600000.SH", "stock_name": "浦发"},
    ], source="text")
    yield repo
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


class FakeProvider:
    name = "fake"

    def get_daily_bars(self, codes, start, end):
        return [
            DailyBar(code=c, trade_date=end, open=10, high=10.5, low=9.8,
                     close=10.2, volume=1000, source="fake")
            for c in codes
        ]

    def get_index_bars(self, codes, start, end):
        return [
            DailyBar(code=c, trade_date=end, open=3800, high=3850, low=3780,
                     close=3820, volume=1e8, source="fake")
            for c in codes
        ]


class EmptyProvider:
    def get_daily_bars(self, codes, start, end):
        return []

    def get_index_bars(self, codes, start, end):
        return []


def test_update_pool_bars(setup):
    repo = setup
    svc = MarketDataService(repo, FakeProvider())
    count = svc.update_pool_bars("default", date(2026, 7, 20))
    assert count == 2
    latest = repo.get_latest_bar_date("000001.SZ")
    assert latest == date(2026, 7, 20)


def test_update_benchmark(setup):
    repo = setup
    svc = MarketDataService(repo, FakeProvider())
    svc.update_benchmark(date(2026, 7, 20), benchmark_codes=["000300.SH"])
    latest = repo.get_latest_bar_date("000300.SH")
    assert latest == date(2026, 7, 20)


def test_explicit_empty_benchmark_codes_is_noop(setup, monkeypatch):
    calls = []

    class RecordingProvider(FakeProvider):
        def get_index_bars(self, codes, start, end):
            calls.append("provider")
            return super().get_index_bars(codes, start, end)

    def record_repository_call(bars):
        calls.append("repository")
        return len(bars)

    monkeypatch.setattr(setup, "upsert_daily_bars", record_repository_call)
    service = MarketDataService(setup, RecordingProvider())

    assert service.update_benchmark(
        date(2026, 7, 20), benchmark_codes=[]
    ) == 0
    assert calls == []


def test_update_empty_codes(setup):
    repo = setup
    svc = MarketDataService(repo, FakeProvider())
    count = svc.update_bars([], date(2026, 7, 20))
    assert count == 0


def test_update_with_none_provider(setup):
    """provider=None 时真实更新必须显式失败。"""
    service = MarketDataService(setup, provider=None)
    with pytest.raises(ProviderUnavailableError):
        service.update_bars(["000001.SZ"], date(2026, 7, 20))


def test_non_empty_stock_request_rejects_empty_provider_result(setup):
    service = MarketDataService(setup, EmptyProvider())
    with pytest.raises(EmptyProviderResultError) as exc:
        service.update_bars(["000001.SZ"], date(2026, 8, 18))
    assert exc.value.code == "EMPTY_PROVIDER_RESULT"
    assert exc.value.details["codes"] == ["000001.SZ"]


def test_non_empty_benchmark_request_rejects_empty_result(setup):
    service = MarketDataService(setup, EmptyProvider())
    with pytest.raises(EmptyProviderResultError):
        service.update_benchmark(
            date(2026, 8, 18), benchmark_codes=["000300.SH"]
        )


def test_empty_stock_request_is_a_valid_noop(setup):
    service = MarketDataService(setup, EmptyProvider())
    assert service.update_bars_range(
        [], date(2026, 7, 1), date(2026, 7, 31)
    ) == 0


def test_update_bars_range_passes_exact_dates(setup):
    calls = []

    class RangeProvider(FakeProvider):
        def get_daily_bars(self, codes, start, end):
            calls.append((codes, start, end))
            return super().get_daily_bars(codes, start, end)

    service = MarketDataService(setup, RangeProvider())
    count = service.update_bars_range(
        ["000001.SZ"], date(2026, 7, 1), date(2026, 7, 31)
    )

    assert count > 0
    assert calls == [
        (["000001.SZ"], date(2026, 7, 1), date(2026, 7, 31))
    ]


def test_refresh_trade_calendar_persists_provider_result(setup):
    class CalendarProvider(FakeProvider):
        name = "fake-calendar"

        def get_trade_calendar(self, start, end):
            return [
                TradeDay(date(2026, 10, 1), False),
                TradeDay(date(2026, 10, 2), False),
            ]

    service = MarketDataService(setup, CalendarProvider())
    count = service.refresh_trade_calendar(
        date(2026, 10, 1), date(2026, 10, 2)
    )

    assert count == 2
    assert setup.get_trade_day(date(2026, 10, 1)).is_open is False


def test_refresh_trade_calendar_maps_provider_failure(setup):
    class BrokenCalendarProvider(FakeProvider):
        def get_trade_calendar(self, start, end):
            raise ProviderUnavailable("all providers unavailable")

    service = MarketDataService(setup, BrokenCalendarProvider())
    with pytest.raises(CalendarUnavailableError):
        service.refresh_trade_calendar(
            date(2026, 10, 1), date(2026, 10, 2)
        )


def test_refresh_trade_calendar_maps_direct_provider_error(setup):
    class BrokenCalendarProvider(FakeProvider):
        def get_trade_calendar(self, start, end):
            raise ProviderError(self.name, "calendar offline", retriable=True)

    service = MarketDataService(setup, BrokenCalendarProvider())
    with pytest.raises(CalendarUnavailableError):
        service.refresh_trade_calendar(
            date(2026, 10, 1), date(2026, 10, 2)
        )


def test_refresh_trade_calendar_rejects_duplicate_dates(setup):
    class DuplicateCalendarProvider(FakeProvider):
        def get_trade_calendar(self, start, end):
            return [
                TradeDay(date(2026, 10, 1), False),
                TradeDay(date(2026, 10, 1), False),
            ]

    service = MarketDataService(setup, DuplicateCalendarProvider())
    with pytest.raises(CalendarUnavailableError):
        service.refresh_trade_calendar(
            date(2026, 10, 1), date(2026, 10, 2)
        )


def test_refresh_trade_calendar_persists_successful_composite_source(setup):
    class BrokenCalendarProvider(FakeProvider):
        name = "eastmoney"

        def get_trade_calendar(self, start, end):
            raise ProviderError(self.name, "calendar down", retriable=True)

    class FallbackCalendarProvider(FakeProvider):
        name = "akshare"

        def get_trade_calendar(self, start, end):
            return [
                TradeDay(date(2026, 10, 1), False),
                TradeDay(date(2026, 10, 2), False),
            ]

    provider = CompositeProvider(
        [BrokenCalendarProvider(), FallbackCalendarProvider()],
        max_retries=1,
        retry_base_delay=0,
    )
    service = MarketDataService(setup, provider)

    assert service.refresh_trade_calendar(
        date(2026, 10, 1), date(2026, 10, 2)
    ) == 2
    conn = setup._conn()
    try:
        source = conn.execute(
            "SELECT source FROM trade_calendar WHERE trade_date = ?",
            ("2026-10-01",),
        ).fetchone()["source"]
    finally:
        conn.close()
    assert source == "akshare"
