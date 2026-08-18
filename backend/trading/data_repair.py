"""污染股票代码的只读审计与显式恢复工具。

默认 CLI 模式只审计。修复模式先验证在线备份，只创建新股池版本并封存旧版本，
从不重写历史计划、旧股池项或旧 K 线。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .domain import normalize_stock_code
from .jobs.backup_database import run_backup
from .repository import TradingRepository


@dataclass(frozen=True)
class SymbolMismatch:
    old_version_id: int
    pool_name: str
    actual_code: str
    expected_code: str
    stock_name: str | None = None


@dataclass
class RepairReport:
    mismatches: list[SymbolMismatch] = field(default_factory=list)
    applied: bool = False
    backup: dict | None = None
    repairs: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class _ReadOnlyAuditRepository:
    """仅实现审计所需查询，连接不执行任何写 PRAGMA。"""

    def __init__(self, db_path: Path):
        self._uri = f"{db_path.resolve().as_uri()}?mode=ro"

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def list_stock_pool_versions(self, *, usable_only: bool = False) -> list[dict]:
        where = " WHERE is_usable=1" if usable_only else ""
        conn = self._conn()
        try:
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='trade_stock_pool_versions'"
            ).fetchone()
            if table_exists is None:
                return []
            rows = conn.execute(
                "SELECT id, pool_name, version_no, source, is_usable, invalid_reason "
                f"FROM trade_stock_pool_versions{where} "
                "ORDER BY version_no DESC"
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def get_stock_pool_version(self, version_id: int) -> dict | None:
        conn = self._conn()
        try:
            version = conn.execute(
                "SELECT * FROM trade_stock_pool_versions WHERE id=?",
                (version_id,),
            ).fetchone()
            if version is None:
                return None
            items = conn.execute(
                "SELECT stock_code, stock_name, sector_name, manual_blacklist, note "
                "FROM trade_stock_pool_items WHERE pool_version_id=? "
                "ORDER BY stock_code",
                (version_id,),
            ).fetchall()
        finally:
            conn.close()
        result = dict(version)
        result["is_usable"] = bool(result["is_usable"])
        result["items"] = [dict(item) for item in items]
        return result


def audit_symbol_mismatches(repo) -> RepairReport:
    """只读扫描仍可用股池，识别股票代码的交易所后缀污染。"""
    mismatches: list[SymbolMismatch] = []
    versions = repo.list_stock_pool_versions(usable_only=True)
    for summary in versions:
        version = repo.get_stock_pool_version(summary["id"])
        if version is None:
            continue
        for item in version.get("items", []):
            actual = item["stock_code"].strip().upper()
            bare = actual.split(".", 1)[0]
            try:
                expected = normalize_stock_code(bare, kind="stock")
            except ValueError:
                continue
            if actual != expected:
                mismatches.append(SymbolMismatch(
                    old_version_id=version["id"],
                    pool_name=version["pool_name"],
                    actual_code=actual,
                    expected_code=expected,
                    stock_name=item.get("stock_name"),
                ))
    return RepairReport(mismatches=mismatches)


def apply_symbol_repairs(
    repo,
    db_path: str,
    *,
    backup_fn=run_backup,
) -> RepairReport:
    """备份验证成功后，以新增版本方式恢复污染股池。"""
    report = audit_symbol_mismatches(repo)
    if not report.mismatches:
        report.applied = True
        return report

    backup = backup_fn(db_path)
    if (
        not isinstance(backup, dict)
        or not backup.get("path")
        or backup.get("integrity_ok") is not True
    ):
        raise RuntimeError("数据库备份或完整性检查失败，拒绝修复")

    report.backup = backup
    mismatches_by_version: dict[int, dict[str, str]] = {}
    for mismatch in report.mismatches:
        mismatches_by_version.setdefault(mismatch.old_version_id, {})[
            mismatch.actual_code
        ] = mismatch.expected_code

    repairs = []
    for old_version_id, replacements in mismatches_by_version.items():
        old_version = repo.get_stock_pool_version(old_version_id)
        if old_version is None:
            continue
        corrected_items = []
        for item in old_version["items"]:
            corrected = dict(item)
            corrected["stock_code"] = replacements.get(
                item["stock_code"].strip().upper(), item["stock_code"]
            )
            corrected_items.append(corrected)

        message = (
            f"股票池版本 {old_version_id} 含错误市场后缀，"
            "已创建独立修复版本"
        )
        repairs.append({
            "old_version_id": old_version_id,
            "corrected_items": corrected_items,
            "message": message,
        })

    report.repairs = repo.apply_symbol_repair_batch(repairs)
    report.applied = True
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="审计并恢复污染的股票市场后缀")
    parser.add_argument("--db", required=True, help="SQLite 数据库路径")
    parser.add_argument(
        "--apply", action="store_true",
        help="验证备份后创建修复版本；默认仅执行只读审计",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库文件不存在: {db_path}")
    if args.apply:
        report = apply_symbol_repairs(
            TradingRepository(str(db_path)), str(db_path)
        )
    else:
        report = audit_symbol_mismatches(_ReadOnlyAuditRepository(db_path))
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
