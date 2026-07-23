import os
import sqlite3
import pytest
from backend.trading.migrations import run_migrations

TEST_DB = "data/test_mig_v3.db"


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


def test_v3_adds_plan_runs_index(tmp_db):
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        indexes = {r[1] for r in conn.execute("PRAGMA index_list('trade_plan_runs')").fetchall()}
    assert "idx_trade_plan_runs_signal_date" in indexes


def test_v3_records_version(tmp_db):
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        versions = {r[0] for r in conn.execute("SELECT version FROM trade_migrations").fetchall()}
    assert 3 in versions


def test_v3_idempotent(tmp_db):
    run_migrations(tmp_db)
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM trade_migrations WHERE version=3").fetchone()[0]
    assert count == 1


def test_v3_preserves_existing_tables(tmp_db):
    """v3 不应破坏 Phase 1/2 已建的表。"""
    run_migrations(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    for expected in ["trade_accounts", "trade_positions", "trade_plan_runs",
                     "trade_plan_items", "trade_strategy_versions"]:
        assert expected in tables
