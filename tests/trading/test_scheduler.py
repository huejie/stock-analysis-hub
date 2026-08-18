"""Scheduler process wiring and schedule-shape tests."""

from datetime import time
from types import SimpleNamespace

from backend.trading.jobs import scheduler
from backend.trading.jobs.backup_database import (
    _cleanup_old_backups,
    run_backup,
)
from backend.trading.jobs.runner import SCHEDULE, SchedulerRunner


def test_schedule_has_all_five_ordered_shanghai_entries():
    assert SCHEDULE == (
        (time(20, 10), "sync_pool_from_hotlist", True),
        (time(20, 15), "update_market_data", True),
        (time(20, 20), "reconcile_orders", True),
        (time(20, 25), "generate_daily_plan", True),
        (time(23, 30), "backup_database", False),
    )


def test_main_loop_polls_runner_tick_without_next_run_calculation(monkeypatch):
    ticks = []
    sleeps = []

    class FakeRunner:
        def tick(self):
            ticks.append("tick")
            return {"created": 0}

    monkeypatch.setattr(scheduler, "build_runner", lambda: FakeRunner())
    monkeypatch.setattr(
        scheduler,
        "settings",
        SimpleNamespace(db_path="unused.db", trading_scheduler_poll_seconds=7),
    )
    monkeypatch.setattr(scheduler.signal, "signal", lambda *args: None)

    def stop_after_poll(seconds):
        sleeps.append(seconds)
        scheduler._loop_running = False

    monkeypatch.setattr(scheduler.time, "sleep", stop_after_poll)
    scheduler._loop_running = True

    scheduler.main_loop()

    assert ticks == ["tick"]
    assert sleeps == [7]
    assert not hasattr(scheduler, "_next_run_time")


def test_build_runner_master_switch_disabled_is_inert_before_setup(
    monkeypatch,
):
    monkeypatch.setattr(
        scheduler,
        "settings",
        SimpleNamespace(trading_enabled=False, db_path="must-not-exist.db"),
    )
    monkeypatch.setattr(
        scheduler,
        "run_migrations",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("disabled scheduler must not migrate")
        ),
    )
    monkeypatch.setattr(
        scheduler,
        "get_provider",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("disabled scheduler must not build provider")
        ),
    )

    assert scheduler.build_runner() is None


def test_main_loop_master_switch_disabled_does_not_build_or_tick(monkeypatch):
    monkeypatch.setattr(
        scheduler,
        "settings",
        SimpleNamespace(trading_enabled=False, db_path="must-not-exist.db"),
    )
    monkeypatch.setattr(
        scheduler,
        "build_runner",
        lambda: (_ for _ in ()).throw(
            AssertionError("disabled scheduler must not build runtime")
        ),
    )

    assert scheduler.main_loop(check_once=True) is None


def test_build_runner_wires_persistent_executor_and_provider(
    monkeypatch, tmp_path
):
    db_path = str(tmp_path / "scheduler.db")
    fake_settings = SimpleNamespace(
        db_path=db_path,
        trading_data_max_missing_ratio=0.05,
    )
    provider = object()

    monkeypatch.setattr(scheduler, "settings", fake_settings)
    monkeypatch.setattr(
        scheduler, "get_provider", lambda configured: provider
    )

    runner = scheduler.build_runner()

    assert isinstance(runner, SchedulerRunner)
    assert runner.provider is provider
    assert runner.data_job_service.repo is runner.repo
    assert runner.data_job_service.market_data_service.provider is provider


def test_backup_creates_file_and_cleanup(tmp_path):
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
    from datetime import date, timedelta

    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    old_date = (date.today() - timedelta(days=40)).isoformat()
    old_file = backup_dir / f"stock_{old_date}.db"
    old_file.write_bytes(b"fake")
    today_file = backup_dir / f"stock_{date.today().isoformat()}.db"
    today_file.write_bytes(b"fake")

    removed = _cleanup_old_backups(backup_dir, 30)

    assert removed == 1
    assert not old_file.exists()
    assert today_file.exists()
