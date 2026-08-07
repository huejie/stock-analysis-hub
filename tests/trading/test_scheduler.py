"""Scheduler 测试(spec §13.1)。

验证任务调度逻辑(不实际执行无限循环)。
"""
import os
from datetime import date, datetime
import pytest
from unittest.mock import patch

from backend.trading.jobs.scheduler import (
    _is_trade_day, _next_run_time, run_task, SCHEDULE,
)
from backend.trading.jobs.backup_database import run_backup, _cleanup_old_backups
from backend.trading.repository import TradingRepository
from backend.trading.migrations import run_migrations

TEST_DB = "data/test_scheduler.db"


@pytest.fixture
def repo():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    yield TradingRepository(TEST_DB)
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


def test_is_trade_day_weekday():
    assert _is_trade_day(date(2026, 7, 22)) is True  # 周三


def test_is_trade_day_weekend():
    assert _is_trade_day(date(2026, 7, 25)) is False  # 周六


def test_next_run_time_finds_next():
    """15:00 时,下一个任务是 20:15 update_market_data。"""
    now = datetime(2026, 7, 22, 15, 0, 0)
    nxt = _next_run_time(now)
    assert nxt is not None
    run_at, name = nxt
    assert name == "update_market_data"
    assert run_at.hour == 20 and run_at.minute == 15


def test_next_run_time_none_after_all():
    """23:31 时,今天任务都跑完了。"""
    now = datetime(2026, 7, 22, 23, 31, 0)
    assert _next_run_time(now) is None


def test_next_run_time_picks_reconcile():
    """20:16 时,update 已过,下一个是 20:20 reconcile_orders。"""
    now = datetime(2026, 7, 22, 20, 16, 0)
    nxt = _next_run_time(now)
    assert nxt is not None
    _, name = nxt
    assert name == "reconcile_orders"


def test_next_run_time_picks_plan():
    """20:21 时,reconcile 已过,下一个是 20:25 generate_daily_plan。"""
    now = datetime(2026, 7, 22, 20, 21, 0)
    nxt = _next_run_time(now)
    assert nxt is not None
    _, name = nxt
    assert name == "generate_daily_plan"


def test_schedule_has_four_tasks():
    assert len(SCHEDULE) == 4
    names = [t[2] for t in SCHEDULE]
    assert "update_market_data" in names
    assert "reconcile_orders" in names
    assert "generate_daily_plan" in names
    assert "backup_database" in names


def test_run_task_unknown_returns_false(repo):
    assert run_task(repo, "nonexistent_task", date(2026, 7, 22)) is False


def test_run_task_generate_plan_no_account_skips(repo):
    """无账户时,计划生成任务优雅跳过(返回 True)。"""
    assert run_task(repo, "generate_daily_plan", date(2026, 7, 22)) is True


def test_backup_creates_file_and_cleanup(tmp_path):
    """备份创建文件 + 清理过期。"""
    # 创建源 DB
    import sqlite3
    src = tmp_path / "src.db"
    with sqlite3.connect(str(src)) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
    backup_dir = tmp_path / "backup"
    result = run_backup(str(src), str(backup_dir))
    assert result["tables"] >= 1
    assert result["size"] > 0
    assert (backup_dir / result["path"].split("/")[-1]).exists()


def test_backup_cleanup_old(tmp_path):
    """清理超过保留期的备份。"""
    import sqlite3
    from datetime import date as _date, timedelta
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    # 创建一个 40 天前的备份文件
    old_date = (_date.today() - timedelta(days=40)).isoformat()
    old_file = backup_dir / f"stock_{old_date}.db"
    old_file.write_bytes(b"fake")
    # 创建今天的
    today_file = backup_dir / f"stock_{_date.today().isoformat()}.db"
    today_file.write_bytes(b"fake")
    removed = _cleanup_old_backups(backup_dir, 30)
    assert removed == 1
    assert not old_file.exists()
    assert today_file.exists()
