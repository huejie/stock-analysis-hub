import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import backend.trading.migrations as migrations
from backend.trading.migrations import MIGRATION_VERSIONS, run_migrations


def _applied_versions(db_path: str) -> list[int]:
    with sqlite3.connect(db_path) as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT version FROM trade_migrations ORDER BY version"
            )
        ]


def _create_v4_database(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for version in range(1, 5):
            statements = getattr(migrations, f"_MIGRATION_{version}_SQL")
            for statement in statements:
                conn.execute(statement)
            metadata = next(
                item for item in MIGRATION_VERSIONS if item["version"] == version
            )
            conn.execute(
                "INSERT INTO trade_migrations (version, name) VALUES (?, ?)",
                (version, metadata["name"]),
            )
        conn.commit()
    finally:
        conn.close()


def test_new_database_applies_v1_through_v6(tmp_path):
    db_path = str(tmp_path / "new.db")

    run_migrations(db_path)

    assert _applied_versions(db_path) == [1, 2, 3, 4, 5, 6]
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        plan_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(trade_plan_runs)")
        }
        pool_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(trade_stock_pool_versions)"
            )
        }

    assert "trade_calendar" in tables
    assert "degraded" in plan_columns
    assert {"is_usable", "invalid_reason"} <= pool_columns


def test_v4_database_upgrades_to_v6_without_losing_existing_rows(tmp_path):
    db_path = str(tmp_path / "v4.db")
    _create_v4_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO trade_stock_pool_versions "
            "(pool_name, version_no, items_hash, source) VALUES (?, ?, ?, ?)",
            ("default", 1, "legacy-hash", "legacy"),
        )
        conn.commit()

    run_migrations(db_path)

    assert _applied_versions(db_path) == [1, 2, 3, 4, 5, 6]
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT is_usable, invalid_reason FROM trade_stock_pool_versions "
            "WHERE items_hash='legacy-hash'"
        ).fetchone()
        degraded = conn.execute(
            "SELECT dflt_value FROM pragma_table_info('trade_plan_runs') "
            "WHERE name='degraded'"
        ).fetchone()

    assert row == (1, None)
    assert degraded[0] == "0"


def test_migrations_are_idempotent_and_wal_enabled(tmp_path):
    db_path = str(tmp_path / "idempotent.db")

    run_migrations(db_path)
    run_migrations(db_path)

    assert _applied_versions(db_path) == [1, 2, 3, 4, 5, 6]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_concurrent_migration_callers_apply_each_version_once(tmp_path):
    db_path = str(tmp_path / "concurrent.db")
    barrier = Barrier(2)

    def migrate() -> None:
        barrier.wait()
        run_migrations(db_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(migrate) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)

    assert _applied_versions(db_path) == [1, 2, 3, 4, 5, 6]
    with sqlite3.connect(db_path) as conn:
        counts = conn.execute(
            "SELECT version, COUNT(*) FROM trade_migrations "
            "GROUP BY version ORDER BY version"
        ).fetchall()
    assert counts == [(version, 1) for version in range(1, 7)]


def test_pending_migrations_roll_back_together_on_failure(tmp_path, monkeypatch):
    db_path = str(tmp_path / "rollback.db")
    _create_v4_database(db_path)
    monkeypatch.setattr(
        migrations,
        "_MIGRATION_6_SQL",
        [
            "ALTER TABLE trade_plan_runs ADD COLUMN degraded INTEGER NOT NULL DEFAULT 0",
            "THIS IS NOT VALID SQL",
        ],
        raising=False,
    )

    with pytest.raises(sqlite3.OperationalError):
        run_migrations(db_path)

    assert _applied_versions(db_path) == [1, 2, 3, 4]
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        plan_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(trade_plan_runs)")
        }
    assert "trade_calendar" not in tables
    assert "degraded" not in plan_columns


def test_connect_closes_connection_when_pragmas_fail(monkeypatch):
    class FakeConnection:
        closed = False

        def close(self):
            self.closed = True

    connection = FakeConnection()
    monkeypatch.setattr(migrations.sqlite3, "connect", lambda path: connection)
    monkeypatch.setattr(
        migrations,
        "_set_pragmas",
        lambda conn: (_ for _ in ()).throw(sqlite3.OperationalError("locked")),
    )

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        migrations._connect("ignored.db")

    assert connection.closed is True
