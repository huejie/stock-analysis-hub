"""热榜集成测试:辅助加分 + 股池同步 + equity created_at 修复。"""
import os
import sqlite3
from datetime import date, timedelta
import pytest

from backend.trading.repository import TradingRepository
from backend.trading.services.pool_service import PoolService
from backend.trading.services.portfolio_service import PortfolioService
from backend.trading.services.plan_service import _hotlist_bonus
from backend.trading.migrations import run_migrations

TEST_DB = "data/test_hotlist.db"


@pytest.fixture
def repo():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    # 测试库无现有表,补建 stock_records(热榜,来自现有 Database 的表结构)
    conn = sqlite3.connect(TEST_DB)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                rank INTEGER NOT NULL,
                stock_name TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                heat_value REAL,
                UNIQUE(date, stock_code)
            )
        """)
        conn.commit()
    finally:
        conn.close()
    yield TradingRepository(TEST_DB)
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


def _seed_hotlist(conn, d, code, name, rank, heat=1000.0):
    conn.execute(
        "INSERT INTO stock_records (date, rank, stock_name, stock_code, heat_value) "
        "VALUES (?, ?, ?, ?, ?)", (d, rank, name, code, heat),
    )


# ---- _hotlist_bonus 纯函数 ----

def test_hotlist_bonus_none():
    assert _hotlist_bonus(None) == 0


def test_hotlist_bonus_single_day():
    info = {"on_list_days": 1, "latest_rank": 8, "rank_improved": False}
    assert _hotlist_bonus(info) == 2  # 在榜基础 2


def test_hotlist_bonus_top3_improved():
    info = {"on_list_days": 3, "latest_rank": 2, "rank_improved": True}
    # 基础2 + 连续2天(+2) + Top3(+3) + 上升(+2) = 9
    assert _hotlist_bonus(info) == 9


def test_hotlist_bonus_caps_at_10():
    info = {"on_list_days": 5, "latest_rank": 1, "rank_improved": True}
    # 基础2 + 连续4(+3 cap) + Top3(+3) + 上升(+2) = 10,封顶
    assert _hotlist_bonus(info) == 10


# ---- 热榜在榜查询 ----

def test_get_hotlist_presence(repo):
    with sqlite3.connect(TEST_DB) as conn:
        today = date.today().isoformat()
        _seed_hotlist(conn, today, "000636", "风华高科", 3)
        _seed_hotlist(conn, (date.today() - timedelta(days=1)).isoformat(), "000636", "风华高科", 5)
    info = repo.get_hotlist_presence(["000636.SZ"], days=5)
    assert "000636" in info
    assert info["000636"]["on_list_days"] == 2
    assert info["000636"]["rank_improved"] is True  # 5 → 3
    assert info["000636"]["latest_rank"] == 3


# ---- 热榜→股池同步 ----

def test_sync_from_hotlist(repo):
    with sqlite3.connect(TEST_DB) as conn:
        today = date.today().isoformat()
        _seed_hotlist(conn, today, "000636", "风华高科", 1)
        _seed_hotlist(conn, today, "600664", "哈药股份", 2)
        _seed_hotlist(conn, today, "830799", "北交所股", 3)   # 北交所,应过滤
        _seed_hotlist(conn, today, "600000", "ST测试", 4)     # ST,应过滤
    svc = PoolService(repo)
    result = svc.sync_from_hotlist("default", top_n=10)
    assert result["items_count"] == 2  # 过滤后只剩 2 只
    version = repo.get_stock_pool_version(result["id"])
    codes = {it["stock_code"] for it in version["items"]}
    assert codes == {"000636.SZ", "600664.SH"}


def test_sync_from_hotlist_treats_bare_0009_code_as_stock(repo):
    with sqlite3.connect(TEST_DB) as conn:
        _seed_hotlist(conn, date.today().isoformat(), "000936", "华西股份", 1)

    result = PoolService(repo).sync_from_hotlist()
    version = repo.get_stock_pool_version(result["id"])

    assert version["items"][0]["stock_code"] == "000936.SZ"


def test_sync_from_hotlist_idempotent(repo):
    with sqlite3.connect(TEST_DB) as conn:
        _seed_hotlist(conn, date.today().isoformat(), "000636", "风华高科", 1)
    svc = PoolService(repo)
    r1 = svc.sync_from_hotlist()
    r2 = svc.sync_from_hotlist()
    assert r1["id"] == r2["id"]
    assert r2["reused"] is True


def test_sync_from_hotlist_empty_raises(repo):
    svc = PoolService(repo)
    with pytest.raises(ValueError, match="热榜无数据"):
        svc.sync_from_hotlist()


# ---- equity created_at 修复 ----

def test_equity_snapshot_has_created_at(repo):
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    ps = PortfolioService(repo)
    snap = ps.compute_and_save_equity_snapshot(acc["id"], date(2026, 8, 13))
    assert "created_at" in snap
    assert snap["created_at"]
