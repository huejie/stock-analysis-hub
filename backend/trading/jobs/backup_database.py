"""数据库备份(spec §13.4)。

使用 SQLite online backup API(不直接复制正在写入的文件)。
保留策略:每日 30 天(Phase 5 简化,周备份留后)。
"""
import hashlib
import logging
import sqlite3
from datetime import date
from pathlib import Path

from ...config import settings

logger = logging.getLogger("trading.backup")


def run_backup(db_path: str, backup_dir: str | None = None) -> dict:
    """执行在线备份。

    返回 {path, size, hash, tables, integrity_ok, integrity_result}。
    """
    bdir = Path(backup_dir or (Path(db_path).parent / "backup"))
    bdir.mkdir(parents=True, exist_ok=True)
    backup_name = f"stock_{date.today().isoformat()}.db"
    backup_path = bdir / backup_name

    # SQLite online backup API(事务一致性,不锁源库)
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(str(backup_path))
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()

    # 必须检查备份文件本身，而不是源库。失败结果也保留在返回值中，
    # 让调用方能够 fail closed 且留下可审计证据。
    try:
        with sqlite3.connect(str(backup_path)) as check:
            integrity_result = [
                row[0] for row in check.execute("PRAGMA integrity_check")
            ]
            integrity_ok = integrity_result == ["ok"]
            tables = check.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
    except sqlite3.DatabaseError as exc:
        integrity_result = [f"{type(exc).__name__}: {exc}"]
        integrity_ok = False
        tables = 0

    size = backup_path.stat().st_size
    h = hashlib.sha256(backup_path.read_bytes()).hexdigest()[:16]

    logger.info(
        "备份完成: %s (%d bytes, %d tables, sha256=%s, integrity_ok=%s)",
        backup_path, size, tables, h, integrity_ok,
    )

    # 清理过期备份(保留 30 天)
    _cleanup_old_backups(bdir, settings.trading_backup_retention_days)

    return {
        "path": str(backup_path),
        "size": size,
        "hash": h,
        "tables": tables,
        "integrity_ok": integrity_ok,
        "integrity_result": integrity_result,
    }


def _cleanup_old_backups(backup_dir: Path, retention_days: int) -> int:
    """删除超过保留期的备份文件。返回删除数。"""
    cutoff = date.today().toordinal() - retention_days
    removed = 0
    for f in backup_dir.glob("stock_*.db"):
        try:
            # 从文件名 stock_YYYY-MM-DD.db 解析日期
            name = f.stem  # stock_YYYY-MM-DD
            d = date.fromisoformat(name.replace("stock_", ""))
            if d.toordinal() < cutoff:
                f.unlink()
                removed += 1
        except (ValueError, IndexError):
            continue
    if removed:
        logger.info("清理过期备份 %d 个(>%d 天)", removed, retention_days)
    return removed
