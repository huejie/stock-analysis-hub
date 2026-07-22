import os
from datetime import date
import pytest

from backend.trading.services.market_data_service import MarketDataService
from backend.trading.repository import TradingRepository
from backend.trading.migrations import run_migrations
from backend.trading.domain import DailyBar

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


def test_update_empty_codes(setup):
    repo = setup
    svc = MarketDataService(repo, FakeProvider())
    count = svc.update_bars([], date(2026, 7, 20))
    assert count == 0


def test_update_with_none_provider(setup):
    """provider=None 时 update_bars 返回 0,不报错。"""
    repo = setup
    svc = MarketDataService(repo, provider=None)
    count = svc.update_bars(["000001.SZ"], date(2026, 7, 20))
    assert count == 0
