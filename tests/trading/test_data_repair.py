import hashlib
import json
import sqlite3
from datetime import date

import pytest

from backend.trading.data_repair import (
    apply_symbol_repairs,
    audit_symbol_mismatches,
    main,
)
from backend.trading.domain import DailyBar
from backend.trading.migrations import run_migrations
from backend.trading.repository import TradingRepository


START = date(2026, 7, 21)
END = date(2026, 7, 22)


def _create_contaminated_database(tmp_path):
    db_path = tmp_path / "repair.db"
    run_migrations(str(db_path))
    repo = TradingRepository(str(db_path))
    pool = repo.create_stock_pool_version(
        "default",
        [{"stock_code": "000936.SH", "stock_name": "华西股份"}],
        source="legacy",
    )
    repo.upsert_daily_bars([
        DailyBar(
            code="000936.SH", trade_date=END,
            open=7.0, high=7.4, low=6.9, close=7.3,
            volume=1_000_000, source="legacy",
        )
    ])
    plan = repo.create_plan_run(
        run_key="historical-contaminated-plan",
        account_id=1,
        signal_date=END.isoformat(),
        target_trade_date="2026-07-23",
        stock_pool_version_id=pool["id"],
        strategy_version_id=1,
        status="PUBLISHED",
        account_snapshot_json={"cash": 100_000},
        data_snapshot_hash="historical",
    )
    return db_path, repo, pool, plan


def _business_state(db_path):
    tables = (
        "trade_stock_pool_versions",
        "trade_stock_pool_items",
        "trade_daily_bars",
        "trade_plan_runs",
        "trade_data_issues",
    )
    with sqlite3.connect(str(db_path)) as conn:
        state = {}
        for table in tables:
            columns = [
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            ]
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            state[table] = {"columns": columns, "rows": rows}
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    digest = hashlib.sha256(db_path.read_bytes()).hexdigest()
    return state, digest


def _pool_status(repo, version_id):
    return next(
        version for version in repo.list_stock_pool_versions()
        if version["id"] == version_id
    )


def test_dry_run_reports_mismatch_without_changing_historical_data(tmp_path):
    db_path, repo, pool, plan = _create_contaminated_database(tmp_path)
    before_state, before_hash = _business_state(db_path)

    report = audit_symbol_mismatches(repo)

    after_state, after_hash = _business_state(db_path)
    assert len(report.mismatches) == 1
    assert report.mismatches[0].actual_code == "000936.SH"
    assert report.mismatches[0].expected_code == "000936.SZ"
    assert report.applied is False
    assert repo.get_stock_pool_version(pool["id"])["is_usable"] is True
    assert repo.get_daily_bars(["000936.SH"], START, END)
    assert repo.get_plan_run(plan["id"]) is not None
    assert after_state == before_state
    assert after_hash == before_hash


def test_apply_creates_corrected_version_and_issue_without_rewriting_history(tmp_path):
    db_path, repo, pool, plan = _create_contaminated_database(tmp_path)
    backup_path = tmp_path / "verified-backup.db"
    backup_calls = []

    def verified_backup(path):
        backup_calls.append(path)
        backup_path.write_bytes(db_path.read_bytes())
        return {"path": str(backup_path), "integrity_ok": True}

    report = apply_symbol_repairs(
        repo, str(db_path), backup_fn=verified_backup,
    )

    versions = repo.list_stock_pool_versions("default")
    corrected = next(v for v in versions if v["source"] == "symbol_repair")
    issues = repo.list_data_issues(severity="BLOCKING")
    assert report.applied is True
    assert backup_calls == [str(db_path)]
    assert backup_path.exists()
    assert repo.get_stock_pool_version(corrected["id"])["items"] == [
        {
            "stock_code": "000936.SZ", "stock_name": "华西股份",
            "sector_name": None, "manual_blacklist": 0, "note": "",
        }
    ]
    assert _pool_status(repo, pool["id"])["is_usable"] == 0
    assert _pool_status(repo, pool["id"])["invalid_reason"] == "SYMBOL_MARKET_MISMATCH"
    assert issues[0]["issue_code"] == "SYMBOL_MARKET_MISMATCH"
    assert issues[0]["source"] == "data_repair"
    assert issues[0]["details"] == {
        "old_version_id": pool["id"], "new_version_id": corrected["id"],
    }
    assert repo.get_daily_bars(["000936.SH"], START, END)
    assert repo.get_plan_run(plan["id"])["stock_pool_version_id"] == pool["id"]
    assert len(repo.list_plan_runs()) == 1


@pytest.mark.parametrize(
    "backup_result",
    [
        None,
        {"path": "", "integrity_ok": True},
        {"path": "backup.db", "integrity_ok": False},
    ],
)
def test_apply_refuses_all_business_writes_when_backup_is_not_verified(
    tmp_path, backup_result,
):
    db_path, repo, pool, plan = _create_contaminated_database(tmp_path)
    before_state, _ = _business_state(db_path)

    with pytest.raises(
        RuntimeError, match="数据库备份或完整性检查失败，拒绝修复"
    ):
        apply_symbol_repairs(
            repo, str(db_path), backup_fn=lambda _: backup_result,
        )

    after_state, _ = _business_state(db_path)
    assert after_state == before_state
    assert _pool_status(repo, pool["id"])["is_usable"] == 1
    assert repo.get_plan_run(plan["id"]) is not None


def test_apply_keeps_business_state_when_backup_creation_raises(tmp_path):
    db_path, repo, pool, plan = _create_contaminated_database(tmp_path)
    before_state, _ = _business_state(db_path)

    def failed_backup(_):
        raise OSError("backup destination unavailable")

    with pytest.raises(OSError, match="backup destination unavailable"):
        apply_symbol_repairs(repo, str(db_path), backup_fn=failed_backup)

    after_state, _ = _business_state(db_path)
    assert after_state == before_state
    assert _pool_status(repo, pool["id"])["is_usable"] == 1
    assert repo.get_plan_run(plan["id"]) is not None


def test_cli_defaults_to_read_only_dry_run(tmp_path, capsys):
    db_path, repo, pool, _ = _create_contaminated_database(tmp_path)
    before_state, _ = _business_state(db_path)

    assert main(["--db", str(db_path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    after_state, _ = _business_state(db_path)
    assert payload["applied"] is False
    assert payload["mismatches"][0]["expected_code"] == "000936.SZ"
    assert after_state == before_state
    assert _pool_status(repo, pool["id"])["is_usable"] == 1


def _create_multiple_polluted_versions(tmp_path):
    db_path = tmp_path / "multi-repair.db"
    run_migrations(str(db_path))
    repo = TradingRepository(str(db_path))
    prior_correct = repo.create_stock_pool_version(
        "default",
        [{"stock_code": "000936.SZ", "stock_name": "华西股份"}],
        source="historical_correct",
    )
    polluted_old = repo.create_stock_pool_version(
        "default",
        [{"stock_code": "000936.SH", "stock_name": "华西股份"}],
        source="legacy_old",
    )
    polluted_new = repo.create_stock_pool_version(
        "default",
        [
            {"stock_code": "000936.SH", "stock_name": "华西股份"},
            {"stock_code": "600001.SH", "stock_name": "邯郸钢铁"},
        ],
        source="legacy_new",
    )
    return db_path, repo, prior_correct, polluted_old, polluted_new


def test_apply_forces_one_new_current_repair_version_per_polluted_version(tmp_path):
    db_path, repo, prior_correct, polluted_old, polluted_new = (
        _create_multiple_polluted_versions(tmp_path)
    )

    report = apply_symbol_repairs(
        repo,
        str(db_path),
        backup_fn=lambda _: {"path": "verified.db", "integrity_ok": True},
    )

    assert [r["old_version_id"] for r in report.repairs] == [
        polluted_old["id"], polluted_new["id"],
    ]
    repaired_old = repo.get_stock_pool_version(report.repairs[0]["new_version_id"])
    repaired_new = repo.get_stock_pool_version(report.repairs[1]["new_version_id"])
    assert repaired_old["id"] != prior_correct["id"]
    assert repaired_old["source"] == "symbol_repair"
    assert repaired_new["source"] == "symbol_repair"
    assert repaired_old["items_hash"] != prior_correct["items_hash"]
    assert [item["stock_code"] for item in repaired_old["items"]] == ["000936.SZ"]
    assert [item["stock_code"] for item in repaired_new["items"]] == [
        "000936.SZ", "600001.SH",
    ]
    versions = repo.list_stock_pool_versions("default")
    assert versions[0]["id"] == repaired_new["id"]
    assert _pool_status(repo, polluted_old["id"])["is_usable"] == 0
    assert _pool_status(repo, polluted_new["id"])["is_usable"] == 0
    assert len(repo.list_data_issues(severity="BLOCKING")) == 2

    # 普通导入仍按规范内容 hash 复用最初的正确版本。
    normal_import = repo.create_stock_pool_version(
        "default",
        [{"stock_code": "000936.SZ", "stock_name": "华西股份"}],
        source="text",
    )
    assert normal_import["reused"] is True
    assert normal_import["id"] == prior_correct["id"]


@pytest.mark.parametrize(
    "trigger_sql",
    [
        """
        CREATE TRIGGER fail_after_repair_version
        BEFORE INSERT ON trade_stock_pool_items
        WHEN (SELECT source FROM trade_stock_pool_versions WHERE id=NEW.pool_version_id)
             = 'symbol_repair'
        BEGIN SELECT RAISE(ABORT, 'item insert failure'); END
        """,
        """
        CREATE TRIGGER fail_after_old_invalidation
        AFTER UPDATE OF is_usable ON trade_stock_pool_versions
        WHEN NEW.is_usable=0 AND OLD.source LIKE 'legacy_%'
        BEGIN SELECT RAISE(ABORT, 'invalidation failure'); END
        """,
        """
        CREATE TRIGGER fail_issue_insert
        BEFORE INSERT ON trade_data_issues
        WHEN NEW.source='data_repair'
        BEGIN SELECT RAISE(ABORT, 'issue failure'); END
        """,
        """
        CREATE TRIGGER fail_later_repair
        BEFORE INSERT ON trade_stock_pool_items
        WHEN NEW.stock_code='600001.SH'
             AND (SELECT source FROM trade_stock_pool_versions WHERE id=NEW.pool_version_id)
                 = 'symbol_repair'
        BEGIN SELECT RAISE(ABORT, 'later repair failure'); END
        """,
    ],
    ids=[
        "after-version-creation",
        "after-invalidation",
        "issue-insertion",
        "later-version-processing",
    ],
)
def test_apply_rolls_back_entire_repair_batch_on_any_stage_failure(
    tmp_path, trigger_sql,
):
    db_path, repo, prior_correct, polluted_old, polluted_new = (
        _create_multiple_polluted_versions(tmp_path)
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(trigger_sql)
    before_state, _ = _business_state(db_path)

    with pytest.raises(sqlite3.IntegrityError):
        apply_symbol_repairs(
            repo,
            str(db_path),
            backup_fn=lambda _: {"path": "verified.db", "integrity_ok": True},
        )

    after_state, _ = _business_state(db_path)
    assert after_state == before_state
    assert _pool_status(repo, polluted_old["id"])["is_usable"] == 1
    assert _pool_status(repo, polluted_new["id"])["is_usable"] == 1
    assert repo.list_data_issues(severity="BLOCKING") == []
    assert [v["source"] for v in repo.list_stock_pool_versions()] == [
        "legacy_new", "legacy_old", "historical_correct",
    ]


def test_cli_dry_run_rejects_nonexistent_database_without_creating_it(tmp_path):
    missing = tmp_path / "missing.db"

    with pytest.raises(FileNotFoundError, match="数据库文件不存在"):
        main(["--db", str(missing)])

    assert not missing.exists()
    assert not (tmp_path / "missing.db-wal").exists()
    assert not (tmp_path / "missing.db-shm").exists()


def test_cli_dry_run_reports_no_mismatches_for_database_without_trading_schema(
    tmp_path, capsys,
):
    db_path = tmp_path / "legacy.db"
    sqlite3.connect(str(db_path)).close()

    assert main(["--db", str(db_path)]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "mismatches": [], "applied": False, "backup": None,
        "repairs": [],
    }


def test_cli_dry_run_does_not_mask_partial_trading_schema(tmp_path):
    db_path = tmp_path / "partial.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "CREATE TABLE trade_stock_pool_versions (id INTEGER PRIMARY KEY)"
        )

    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        main(["--db", str(db_path)])


def test_cli_dry_run_keeps_non_wal_database_byte_identical(tmp_path, capsys):
    db_path, _, _, _, _ = _create_multiple_polluted_versions(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()
    before_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()

    assert main(["--db", str(db_path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    after_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
    with sqlite3.connect(str(db_path)) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert payload["applied"] is False
    assert after_hash == before_hash
    assert journal_mode == "delete"
    assert not db_path.with_name(db_path.name + "-wal").exists()
    assert not db_path.with_name(db_path.name + "-shm").exists()
