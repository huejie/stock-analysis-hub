"""SQLite 与交易 schema 的只读健康检查。"""
import sqlite3
from pathlib import Path

from .migrations import MIGRATION_VERSIONS


_REQUIRED_COLUMNS = {
    "trade_migrations": frozenset({"version", "name", "applied_at"}),
    "trade_stock_pool_versions": frozenset(
        {"id", "pool_name", "version_no", "is_usable", "invalid_reason"}
    ),
    "trade_stock_pool_items": frozenset(
        {"pool_version_id", "stock_code"}
    ),
    "trade_daily_bars": frozenset(
        {"stock_code", "trade_date", "close", "source", "checksum"}
    ),
    "trade_data_issues": frozenset(
        {"id", "severity", "issue_code", "details_json"}
    ),
    "trade_jobs": frozenset(
        {
            "id",
            "job_type",
            "job_key",
            "status",
            "request_json",
            "result_json",
            "error_json",
            "attempts",
        }
    ),
    "trade_job_locks": frozenset(
        {"lock_key", "owner_id", "expires_at"}
    ),
    "trade_strategy_versions": frozenset(
        {"id", "strategy_code", "version_no", "status"}
    ),
    "trade_plan_runs": frozenset(
        {"id", "run_key", "status", "degraded"}
    ),
    "trade_plan_items": frozenset(
        {"id", "plan_run_id", "stock_code", "action"}
    ),
    "trade_accounts": frozenset({"id", "name", "cash_balance"}),
    "trade_positions": frozenset(
        {"id", "account_id", "stock_code", "available_quantity"}
    ),
    "trade_executions": frozenset(
        {"id", "account_id", "stock_code", "client_execution_id"}
    ),
    "trade_equity_snapshots": frozenset(
        {"account_id", "trade_date", "total_equity"}
    ),
    "trade_audit_logs": frozenset({"id", "action", "entity_type"}),
    "trade_backtest_runs": frozenset({"id", "job_id", "status"}),
    "trade_backtest_trades": frozenset(
        {"id", "backtest_run_id", "stock_code"}
    ),
    "trade_calendar": frozenset(
        {"trade_date", "exchange", "is_open", "source"}
    ),
}


def _read_only_connection(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"数据库文件不存在: {path}")
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def check_sqlite_database(db_path: str) -> dict[str, object]:
    """验证配置路径可只读打开且 SQLite 完整性检查通过。"""
    try:
        conn = _read_only_connection(db_path)
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()
            integrity = result[0] if result else "no result"
            if integrity != "ok":
                raise sqlite3.DatabaseError(
                    f"SQLite quick_check failed: {integrity}"
                )
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}
    return {"status": "ok", "integrity": "ok"}


def check_trading_schema(db_path: str) -> dict[str, object]:
    """核对当前迁移版本、必需表及关键列，不创建或修复文件。"""
    expected_migrations = [
        {"version": item["version"], "name": item["name"]}
        for item in MIGRATION_VERSIONS
    ]
    applied_migrations: list[dict[str, object]] = []
    existing_tables: set[str] = set()
    existing_columns: dict[str, set[str]] = {}
    path = Path(db_path)

    if path.is_file():
        conn = _read_only_connection(db_path)
        try:
            existing_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "trade_migrations" in existing_tables:
                applied_migrations = [
                    {"version": row[0], "name": row[1]}
                    for row in conn.execute(
                        "SELECT version, name FROM trade_migrations "
                        "ORDER BY version"
                    ).fetchall()
                ]
            for table in _REQUIRED_COLUMNS.keys() & existing_tables:
                existing_columns[table] = {
                    row[1]
                    for row in conn.execute(
                        f'PRAGMA table_info("{table}")'
                    ).fetchall()
                }
        finally:
            conn.close()

    expected_by_version = {
        item["version"]: item["name"] for item in expected_migrations
    }
    applied_by_version = {
        item["version"]: item["name"] for item in applied_migrations
    }
    missing_versions = sorted(
        expected_by_version.keys() - applied_by_version.keys()
    )
    unexpected_versions = sorted(
        applied_by_version.keys() - expected_by_version.keys()
    )
    name_mismatches = [
        {
            "version": version,
            "expected_name": expected_by_version[version],
            "applied_name": applied_by_version[version],
        }
        for version in sorted(
            expected_by_version.keys() & applied_by_version.keys()
        )
        if expected_by_version[version] != applied_by_version[version]
    ]
    missing_tables = sorted(_REQUIRED_COLUMNS.keys() - existing_tables)
    missing_columns = {
        table: sorted(required - existing_columns.get(table, set()))
        for table, required in _REQUIRED_COLUMNS.items()
        if table in existing_tables
        and required - existing_columns.get(table, set())
    }
    migration_history_matches = applied_migrations == expected_migrations
    ready = (
        migration_history_matches
        and not missing_tables
        and not missing_columns
    )
    return {
        "status": "ok" if ready else "error",
        "expected_version": max(expected_by_version, default=0),
        "applied_version": max(applied_by_version, default=0),
        "expected_migrations": expected_migrations,
        "applied_migrations": applied_migrations,
        "missing_versions": missing_versions,
        "unexpected_versions": unexpected_versions,
        "name_mismatches": name_mismatches,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
    }
