import os
from datetime import date
import pytest

from backend.trading.repository import TradingRepository
from backend.trading.migrations import run_migrations
from backend.trading.domain import DailyBar

TEST_DB = "data/test_trading_repo.db"


@pytest.fixture
def repo():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    r = TradingRepository(TEST_DB)
    yield r
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


def test_create_stock_pool_version(repo):
    items = [
        {"stock_code": "000001.SZ", "stock_name": "平安银行"},
        {"stock_code": "600000.SH", "stock_name": "浦发银行"},
    ]
    version = repo.create_stock_pool_version("default", items, source="text")
    assert version["id"] > 0
    assert version["version_no"] == 1
    assert version["items_hash"]
    assert version["items_count"] == 2


def test_stock_pool_version_dedup_same_items(repo):
    items = [{"stock_code": "000001.SZ", "stock_name": "x"}]
    v1 = repo.create_stock_pool_version("default", items, source="text")
    v2 = repo.create_stock_pool_version("default", items, source="text")
    # 相同 items_hash 应返回已存在的版本,不新建
    assert v1["id"] == v2["id"]
    assert v2["version_no"] == 1


def test_stock_pool_version_increments(repo):
    items1 = [{"stock_code": "000001.SZ", "stock_name": "x"}]
    items2 = [{"stock_code": "000001.SZ", "stock_name": "x"},
              {"stock_code": "600000.SH", "stock_name": "y"}]
    v1 = repo.create_stock_pool_version("default", items1, source="text")
    v2 = repo.create_stock_pool_version("default", items2, source="text")
    assert v2["version_no"] == v1["version_no"] + 1


def test_get_stock_pool_version(repo):
    items = [{"stock_code": "000001.SZ", "stock_name": "平安银行", "sector_name": "银行"}]
    v = repo.create_stock_pool_version("default", items, source="text")
    fetched = repo.get_stock_pool_version(v["id"])
    assert fetched["version_no"] == 1
    assert len(fetched["items"]) == 1
    assert fetched["items"][0]["stock_code"] == "000001.SZ"
    assert fetched["items"][0]["stock_name"] == "平安银行"


def test_list_stock_pool_versions(repo):
    repo.create_stock_pool_version("default", [{"stock_code": "000001.SZ"}], source="text")
    repo.create_stock_pool_version("default", [{"stock_code": "000001.SZ"}, {"stock_code": "600000.SH"}], source="text")
    versions = repo.list_stock_pool_versions("default")
    assert len(versions) == 2
    assert versions[0]["version_no"] == 2  # 降序


def test_upsert_daily_bars(repo):
    bars = [
        DailyBar(code="000001.SZ", trade_date=date(2026, 7, 20),
                 open=10.0, high=10.5, low=9.8, close=10.2, volume=1000,
                 amount=10000, adjust_factor=1.0, source="eastmoney"),
    ]
    inserted = repo.upsert_daily_bars(bars)
    assert inserted == 1
    fetched = repo.get_daily_bars(["000001.SZ"], date(2026, 7, 1), date(2026, 7, 31))
    assert len(fetched) == 1
    assert fetched[0]["close"] == 10.2


def test_get_latest_bar_date(repo):
    bars = [
        DailyBar(code="000001.SZ", trade_date=date(2026, 7, 18),
                 open=1, high=1, low=1, close=1, volume=1, source="eastmoney"),
        DailyBar(code="000001.SZ", trade_date=date(2026, 7, 20),
                 open=1, high=1, low=1, close=1, volume=1, source="eastmoney"),
    ]
    repo.upsert_daily_bars(bars)
    latest = repo.get_latest_bar_date("000001.SZ")
    assert latest == date(2026, 7, 20)


def test_get_latest_bar_date_none(repo):
    assert repo.get_latest_bar_date("999999.SZ") is None


def test_pool_missing_codes(repo):
    items = [{"stock_code": "000001.SZ"}, {"stock_code": "600000.SH"}]
    repo.create_stock_pool_version("default", items, source="text")
    # 只给 000001 补了行情,600000 缺失
    repo.upsert_daily_bars([DailyBar(code="000001.SZ", trade_date=date(2026, 7, 20),
                                     open=1, high=1, low=1, close=1, volume=1, source="x")])
    missing = repo.pool_missing_codes("default", date(2026, 7, 20))
    assert missing == ["600000.SH"]


def test_create_and_get_data_issues(repo):
    repo.create_data_issue(severity="WARNING", issue_code="PRICE_CONFLICT",
                           message="价格冲突", stock_code="000001.SZ",
                           trade_date="2026-07-20", details={"diff": 0.01})
    issues = repo.list_data_issues("2026-07-20")
    assert len(issues) == 1
    assert issues[0]["severity"] == "WARNING"


def test_create_and_get_job(repo):
    jid = repo.create_job(job_type="update_bars", job_key="key1",
                          request={"trade_date": "2026-07-20"})
    assert jid > 0
    job = repo.get_job(jid)
    assert job["status"] == "QUEUED"
    assert job["request"]["trade_date"] == "2026-07-20"


def test_create_job_idempotent(repo):
    """相同 job_key 且未完成时,复用已有 job。"""
    jid1 = repo.create_job(job_type="update_bars", job_key="key1", request={})
    jid2 = repo.create_job(job_type="update_bars", job_key="key1", request={})
    assert jid1 == jid2
