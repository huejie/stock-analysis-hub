import os
from datetime import date
import pytest

from backend.trading.services.market_data_service import MarketDataService
from backend.trading.repository import TradingRepository
from backend.trading.migrations import run_migrations
from backend.trading.domain import DailyBar

TEST_DB = "data/test_dq.db"


@pytest.fixture
def repo():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    return TradingRepository(TEST_DB)


def _bar(code, d):
    return DailyBar(code=code, trade_date=d, open=10, high=10.5, low=9.8,
                    close=10.2, volume=1000, source="test")


def test_health_blocked_when_benchmark_missing(repo):
    """基准缺失 -> BLOCKED。"""
    repo.create_stock_pool_version("default", [{"stock_code": "000001.SZ"}], source="text")
    repo.upsert_daily_bars([_bar("000001.SZ", date(2026, 7, 20))])
    svc = MarketDataService(repo, provider=None)
    health = svc.check_data_health(
        trade_date=date(2026, 7, 20),
        pool_name="default",
        benchmark_codes=["000300.SH"],
    )
    assert health["overall_status"] == "BLOCKED"
    assert health["pool_missing_ratio"] == 0.0
    assert health["benchmark_updated"]["000300.SH"] is False


def test_health_ok_when_all_updated(repo):
    """基准+池全部就绪 -> OK。"""
    repo.create_stock_pool_version("default", [{"stock_code": "000001.SZ"}], source="text")
    repo.upsert_daily_bars([
        _bar("000001.SZ", date(2026, 7, 20)),
        _bar("000300.SH", date(2026, 7, 20)),
    ])
    svc = MarketDataService(repo, provider=None)
    health = svc.check_data_health(
        trade_date=date(2026, 7, 20),
        pool_name="default",
        benchmark_codes=["000300.SH"],
    )
    assert health["overall_status"] == "OK"
    assert health["benchmark_updated"]["000300.SH"] is True
    assert health["pool_missing"] == []


def test_health_partial_when_small_missing(repo):
    """20 只池缺 1 只(5%,等于阈值) -> PARTIAL。"""
    items = [{"stock_code": f"{i:06d}.SZ"} for i in range(20)]
    repo.create_stock_pool_version("default", items, source="text")
    bars = [_bar(f"{i:06d}.SZ", date(2026, 7, 20)) for i in range(19)]  # 缺 1 只
    bars.append(_bar("000300.SH", date(2026, 7, 20)))
    repo.upsert_daily_bars(bars)
    svc = MarketDataService(repo, provider=None)
    health = svc.check_data_health(
        trade_date=date(2026, 7, 20),
        pool_name="default",
        benchmark_codes=["000300.SH"],
    )
    # 1/20 = 5%,等于阈值(不超过),应为 PARTIAL
    assert health["overall_status"] == "PARTIAL"
    assert health["pool_missing_ratio"] == 0.05


def test_health_blocked_when_missing_exceeds_threshold(repo):
    """10 只池缺 1 只(10%,超过阈值) -> BLOCKED。"""
    items = [{"stock_code": f"{i:06d}.SZ"} for i in range(10)]
    repo.create_stock_pool_version("default", items, source="text")
    bars = [_bar(f"{i:06d}.SZ", date(2026, 7, 20)) for i in range(9)]  # 缺 1 只
    bars.append(_bar("000300.SH", date(2026, 7, 20)))
    repo.upsert_daily_bars(bars)
    svc = MarketDataService(repo, provider=None)
    health = svc.check_data_health(
        trade_date=date(2026, 7, 20),
        pool_name="default",
        benchmark_codes=["000300.SH"],
    )
    assert health["overall_status"] == "BLOCKED"


def test_health_blocked_when_pool_empty(repo):
    """空池 -> BLOCKED。"""
    svc = MarketDataService(repo, provider=None)
    health = svc.check_data_health(
        trade_date=date(2026, 7, 20),
        pool_name="default",
        benchmark_codes=["000300.SH"],
    )
    assert health["overall_status"] == "BLOCKED"
