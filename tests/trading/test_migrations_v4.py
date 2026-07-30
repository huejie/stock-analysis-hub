import os
import sqlite3
import pytest
from backend.trading.migrations import run_migrations

TEST_DB = "data/test_mig_v4.db"


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


def test_v4_creates_backtest_tables(tmp_db):
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "trade_backtest_runs" in tables
    assert "trade_backtest_trades" in tables


def test_v4_records_version(tmp_db):
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        versions = {r[0] for r in conn.execute("SELECT version FROM trade_migrations").fetchall()}
    assert 4 in versions


def test_v4_idempotent(tmp_db):
    run_migrations(tmp_db)
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM trade_migrations WHERE version=4").fetchone()[0]
    assert count == 1


def test_v4_backtest_trades_index(tmp_db):
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        indexes = {r[1] for r in conn.execute("PRAGMA index_list('trade_backtest_trades')").fetchall()}
    assert "idx_trade_backtest_trades_run" in indexes
