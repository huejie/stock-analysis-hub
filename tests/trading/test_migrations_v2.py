import os
import sqlite3
import pytest
from backend.trading.migrations import run_migrations, MIGRATION_VERSIONS

TEST_DB = "data/test_trading_mig_v2.db"


@pytest.fixture
def tmp_db():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    yield TEST_DB
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


def test_v2_creates_account_tables(tmp_db):
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    for expected in [
        # Phase 2 主表
        "trade_accounts", "trade_positions", "trade_executions", "trade_equity_snapshots",
        # Phase 3 预建空表(保证 FK 完整性)
        "trade_strategy_versions", "trade_plan_runs", "trade_plan_items", "trade_audit_logs",
        # Phase 1 已建(确认仍在)
        "trade_stock_pool_versions", "trade_daily_bars", "trade_jobs",
    ]:
        assert expected in tables, f"缺少表: {expected}"


def test_v2_records_migration_version(tmp_db):
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT version, name FROM trade_migrations ORDER BY version"
        ).fetchall()
    versions = {r[0]: r[1] for r in rows}
    assert 1 in versions
    assert 2 in versions


def test_v2_idempotent(tmp_db):
    run_migrations(tmp_db)
    run_migrations(tmp_db)  # 重复不应报错
    with sqlite3.connect(tmp_db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM trade_migrations WHERE version = 2"
        ).fetchone()[0]
    assert count == 1  # 不重复记录


def test_v2_executions_has_client_execution_id(tmp_db):
    """Phase 2 决策:成交幂等键列存在。"""
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_executions)").fetchall()}
    assert "client_execution_id" in cols


def test_v2_fk_to_plan_items_works(tmp_db):
    """trade_executions.plan_item_id FK 引用 trade_plan_items(Phase 3 空表)。"""
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        # 插入一个 account + plan_run + plan_item + execution(FK 链)
        conn.execute("INSERT INTO trade_accounts (name, initial_equity, cash_balance, created_at, updated_at) VALUES ('a', 100000, 100000, '2026-07-22', '2026-07-22')")
        conn.execute("INSERT INTO trade_strategy_versions (strategy_code, version_no, name, params_json, params_hash, status, created_at) VALUES ('default', 1, 'v1', '{}', 'h1', 'ACTIVE', '2026-07-22')")
        conn.execute("INSERT INTO trade_stock_pool_versions (pool_name, version_no, items_hash, source, created_at) VALUES ('default', 1, 'h1', 'text', '2026-07-22')")
        conn.execute("""INSERT INTO trade_plan_runs (run_key, account_id, signal_date, target_trade_date, stock_pool_version_id, strategy_version_id, status, account_snapshot_json, data_snapshot_hash, created_at)
                        VALUES ('k1', 1, '2026-07-22', '2026-07-22', 1, 1, 'READY', '{}', 'h', '2026-07-22')""")
        conn.execute("""INSERT INTO trade_plan_items (plan_run_id, stock_code, action, created_at) VALUES (1, '000001.SZ', 'CONDITIONAL_BUY', '2026-07-22')""")
        conn.execute("""INSERT INTO trade_executions (account_id, plan_item_id, stock_code, side, trade_date, price, quantity, client_execution_id, created_at)
                        VALUES (1, 1, '000001.SZ', 'BUY', '2026-07-22', 10.0, 100, 'c1', '2026-07-22')""")
