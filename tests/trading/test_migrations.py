import os
import sqlite3
import pytest
from backend.trading.migrations import run_migrations, MIGRATION_VERSIONS

TEST_DB = "data/test_trading_migrations.db"


@pytest.fixture
def tmp_db():
    os.makedirs("data", exist_ok=True)
    # 强制回收上一测试可能残留的 sqlite 连接(Windows 文件锁)
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            # WAL 残留连接未释放:跳过清理,后续用例在已有库上跑仍能验证幂等
            pass
    yield TEST_DB
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


def test_migrations_create_tables(tmp_db):
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    for expected in ["trade_migrations", "trade_stock_pool_versions",
                     "trade_stock_pool_items", "trade_daily_bars",
                     "trade_data_issues", "trade_jobs", "trade_job_locks"]:
        assert expected in tables, f"缺少表: {expected}"


def test_migrations_idempotent(tmp_db):
    """重复执行迁移不应报错,版本表记录不变。"""
    run_migrations(tmp_db)
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT version FROM trade_migrations ORDER BY version"
        ).fetchall()
    applied = {r[0] for r in rows}
    assert applied == {m["version"] for m in MIGRATION_VERSIONS}


def test_migrations_wal_enabled(tmp_db):
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    # SQLite 可能返回 'wal' 或在只读场景返回小写
    assert journal.lower() == "wal"
