from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Barrier, Event, Lock, Thread
from types import SimpleNamespace

import pytest

from backend.trading.domain import TradeDay
from backend.trading.jobs.runner import (
    SCHEDULER_LOCK_KEY,
    SHANGHAI_TIMEZONE,
    SchedulerRunner,
)
from backend.trading.migrations import run_migrations
from backend.trading.providers.base import ProviderError, ProviderUnavailable
from backend.trading.repository import TradingRepository


class CalendarProvider:
    name = "trusted-calendar"

    def __init__(
        self,
        *,
        is_open: bool = True,
        error: Exception | None = None,
        uncovered: bool = False,
    ):
        self.is_open = is_open
        self.error = error
        self.uncovered = uncovered
        self.calls = []

    def get_trade_calendar(self, start, end):
        self.calls.append((start, end))
        if self.error is not None:
            raise self.error
        if self.uncovered:
            return []
        return [TradeDay(start, self.is_open)]


class RecordingDataJobService:
    def __init__(self, repo):
        self.repo = repo
        self.calls = []

    def run_next_queued_job(self, owner_id, now):
        self.calls.append((owner_id, now))
        return None


class AdjustableMonotonic:
    def __init__(self):
        self._value = 0.0
        self._lock = Lock()

    def __call__(self):
        with self._lock:
            return self._value

    def set(self, value):
        with self._lock:
            self._value = float(value)


class ControlledHeartbeatWait:
    """Wake a heartbeat deterministically without sleeping."""

    def __init__(self):
        self.ready = Event()
        self.trigger = Event()

    def __call__(self, stop, interval):
        self.ready.set()
        while not self.trigger.is_set():
            if stop.wait(timeout=0.05):
                return True
        self.trigger.clear()
        return stop.is_set()


class BlockingDataJobService:
    def __init__(self):
        self.entered = Event()
        self.release = Event()
        self.calls = []

    def run_next_queued_job(self, owner_id, now):
        self.calls.append((owner_id, now))
        self.entered.set()
        assert self.release.wait(timeout=3), "executor release was not signalled"
        return None


class ObservedRenewalRepository:
    def __init__(self, repo, owner_id, *, transfer_on_renewal=False):
        self.repo = repo
        self.owner_id = owner_id
        self.transfer_on_renewal = transfer_on_renewal
        self.acquire_calls = 0
        self.renewed = Event()
        self.transferred = Event()
        self.release_owners = []

    def __getattr__(self, name):
        return getattr(self.repo, name)

    def acquire_job_lock(self, **kwargs):
        self.acquire_calls += 1
        if (
            self.acquire_calls > 1
            and kwargs["owner_id"] == self.owner_id
        ):
            if self.transfer_on_renewal:
                self.repo.release_job_lock(
                    lock_key=kwargs["lock_key"],
                    owner_id=self.owner_id,
                )
                acquired = self.repo.acquire_job_lock(
                    lock_key=kwargs["lock_key"],
                    owner_id="runner-b",
                    now=kwargs["now"],
                    expires_at=kwargs["now"] + timedelta(minutes=10),
                )
                if not acquired:
                    raise AssertionError("failed to transfer scheduler lock")
                self.transferred.set()
                return False
            acquired = self.repo.acquire_job_lock(**kwargs)
            if acquired:
                self.renewed.set()
            return acquired
        return self.repo.acquire_job_lock(**kwargs)

    def release_job_lock(self, **kwargs):
        self.release_owners.append(kwargs["owner_id"])
        return self.repo.release_job_lock(**kwargs)


@pytest.fixture
def setup(tmp_path):
    db_path = str(tmp_path / "scheduler.db")
    run_migrations(db_path)
    repo = TradingRepository(db_path)
    settings = SimpleNamespace(
        db_path=db_path,
        trading_timezone="Asia/Shanghai",
        trading_schedule_enabled=True,
        trading_stale_job_seconds=1800,
        trading_job_lock_ttl_seconds=300,
        trading_scheduler_poll_seconds=30,
    )
    return repo, settings


def _runner(
    repo,
    settings,
    provider=None,
    *,
    owner="runner-a",
    data_job_service=None,
):
    service = data_job_service or RecordingDataJobService(repo)
    return SchedulerRunner(
        repo,
        service,
        provider or CalendarProvider(),
        settings,
        owner_id=owner,
    )


@pytest.mark.parametrize(
    ("hour", "minute", "expected_types"),
    [
        (
            20,
            16,
            ["sync_pool_from_hotlist", "update_market_data"],
        ),
        (
            20,
            21,
            [
                "sync_pool_from_hotlist",
                "update_market_data",
                "reconcile_orders",
            ],
        ),
        (
            23,
            31,
            [
                "sync_pool_from_hotlist",
                "update_market_data",
                "reconcile_orders",
                "generate_daily_plan",
                "backup_database",
            ],
        ),
    ],
)
def test_late_tick_creates_every_due_job(
    setup, hour, minute, expected_types
):
    repo, settings = setup
    runner = _runner(repo, settings)
    now = datetime(
        2026, 8, 18, hour, minute, tzinfo=SHANGHAI_TIMEZONE
    )

    summary = runner.tick(now)

    assert summary["created"] == len(expected_types)
    for job_type in expected_types:
        job = repo.get_job_by_key(
            f"schedule:{job_type}:2026-08-18"
        )
        assert job is not None
        assert job["job_type"] == job_type
        assert job["request"]["trade_date"] == "2026-08-18"


def test_repeat_tick_and_restart_reuse_deterministic_schedule_keys(setup):
    repo, settings = setup
    now = datetime(2026, 8, 18, 20, 16, tzinfo=SHANGHAI_TIMEZONE)
    first = _runner(repo, settings, owner="runner-a")

    assert first.tick(now)["created"] == 2
    assert first.tick(now + timedelta(seconds=30))["created"] == 0

    restarted = _runner(
        TradingRepository(settings.db_path), settings, owner="runner-b"
    )
    assert restarted.tick(now + timedelta(minutes=1))["created"] == 0
    assert repo.get_job_by_key(
        "schedule:sync_pool_from_hotlist:2026-08-18"
    )["id"] == 1
    assert repo.get_job_by_key(
        "schedule:update_market_data:2026-08-18"
    )["id"] == 2


def test_aware_utc_input_is_normalized_to_shanghai_date_and_time(setup):
    repo, settings = setup
    runner = _runner(repo, settings)

    summary = runner.tick(
        datetime(2026, 8, 18, 12, 16, tzinfo=timezone.utc)
    )

    assert summary["created"] == 2
    assert repo.get_job_by_key(
        "schedule:update_market_data:2026-08-18"
    ) is not None


def test_naive_input_is_interpreted_as_shanghai_local_time(setup):
    repo, settings = setup
    runner = _runner(repo, settings)

    summary = runner.tick(datetime(2026, 8, 18, 20, 16))

    assert summary["created"] == 2


def test_cached_trusted_calendar_avoids_provider_call(setup):
    repo, settings = setup
    repo.upsert_trade_calendar(
        [TradeDay(date(2026, 8, 18), True)], source="akshare"
    )
    provider = CalendarProvider(
        error=ProviderUnavailable("must not be called")
    )
    runner = _runner(repo, settings, provider)

    summary = runner.tick(
        datetime(2026, 8, 18, 20, 16, tzinfo=SHANGHAI_TIMEZONE)
    )

    assert summary["calendar_status"] == "open"
    assert provider.calls == []
    assert summary["created"] == 2


def test_closed_day_creates_only_backup(setup):
    repo, settings = setup
    runner = _runner(repo, settings, CalendarProvider(is_open=False))

    summary = runner.tick(
        datetime(2026, 10, 1, 23, 31, tzinfo=SHANGHAI_TIMEZONE)
    )

    assert summary["calendar_status"] == "closed"
    assert summary["created"] == 1
    assert repo.get_job_by_key(
        "schedule:backup_database:2026-10-01"
    ) is not None
    for job_type in (
        "sync_pool_from_hotlist",
        "update_market_data",
        "reconcile_orders",
        "generate_daily_plan",
    ):
        assert repo.get_job_by_key(
            f"schedule:{job_type}:2026-10-01"
        ) is None


@pytest.mark.parametrize(
    "provider",
    [
        CalendarProvider(error=ProviderUnavailable("calendar offline")),
        CalendarProvider(
            error=ProviderError(
                "akshare", "calendar offline", retriable=False
            )
        ),
        CalendarProvider(uncovered=True),
    ],
)
def test_unavailable_or_uncovered_calendar_fails_closed(
    setup, provider
):
    repo, settings = setup
    runner = _runner(repo, settings, provider)

    summary = runner.tick(
        datetime(2026, 10, 1, 20, 26, tzinfo=SHANGHAI_TIMEZONE)
    )

    assert summary["calendar_status"] == "unavailable"
    assert summary["blocked_reason"] == "CALENDAR_UNAVAILABLE"
    assert summary["created"] == 0
    assert repo.get_job_by_key(
        "schedule:generate_daily_plan:2026-10-01"
    ) is None
    issues = repo.list_data_issues(
        trade_date="2026-10-01", severity="BLOCKING"
    )
    assert [issue["issue_code"] for issue in issues] == [
        "CALENDAR_UNAVAILABLE"
    ]


def test_calendar_failure_still_allows_daily_backup(setup):
    repo, settings = setup
    runner = _runner(
        repo,
        settings,
        CalendarProvider(error=ProviderUnavailable("calendar offline")),
    )

    summary = runner.tick(
        datetime(2026, 10, 1, 23, 31, tzinfo=SHANGHAI_TIMEZONE)
    )

    assert summary["created"] == 1
    assert repo.get_job_by_key(
        "schedule:backup_database:2026-10-01"
    ) is not None


def test_scheduler_disabled_consumes_queue_without_calendar_or_creation(
    setup,
):
    repo, settings = setup
    settings.trading_schedule_enabled = False
    provider = CalendarProvider(error=ProviderUnavailable("offline"))
    service = RecordingDataJobService(repo)
    runner = _runner(
        repo, settings, provider, data_job_service=service
    )

    summary = runner.tick(
        datetime(2026, 8, 18, 23, 31, tzinfo=SHANGHAI_TIMEZONE)
    )

    assert summary["created"] == 0
    assert summary["calendar_status"] == "not_checked"
    assert len(service.calls) == 1
    assert provider.calls == []


def test_held_persistent_scheduler_lock_blocks_scheduling_and_execution(
    setup,
):
    repo, settings = setup
    now = datetime(2026, 8, 18, 20, 16, tzinfo=SHANGHAI_TIMEZONE)
    assert repo.acquire_job_lock(
        lock_key="scheduler:tick",
        owner_id="runner-a",
        now=now,
        expires_at=now + timedelta(minutes=5),
    )
    service = RecordingDataJobService(repo)
    runner = _runner(
        TradingRepository(settings.db_path),
        settings,
        owner="runner-b",
        data_job_service=service,
    )

    summary = runner.tick(now)

    assert summary["lock_acquired"] is False
    assert summary["created"] == 0
    assert service.calls == []


def test_scheduler_lock_is_released_when_executor_raises(setup):
    repo, settings = setup

    class FailingService:
        def run_next_queued_job(self, owner_id, now):
            raise RuntimeError("boom")

    runner = _runner(
        repo, settings, data_job_service=FailingService()
    )
    now = datetime(2026, 8, 18, 19, 0, tzinfo=SHANGHAI_TIMEZONE)

    with pytest.raises(RuntimeError, match="boom"):
        runner.tick(now)

    assert repo.acquire_job_lock(
        lock_key="scheduler:tick",
        owner_id="probe",
        now=now,
        expires_at=now + timedelta(minutes=1),
    )


def test_scheduler_heartbeat_keeps_long_tick_exclusive_past_original_ttl(
    setup,
):
    repo, settings = setup
    settings.trading_job_lock_ttl_seconds = 300
    clock = AdjustableMonotonic()
    heartbeat_wait = ControlledHeartbeatWait()
    observed_repo = ObservedRenewalRepository(repo, "runner-a")
    service_a = BlockingDataJobService()
    runner_a = SchedulerRunner(
        observed_repo,
        service_a,
        CalendarProvider(),
        settings,
        owner_id="runner-a",
        monotonic_fn=clock,
        heartbeat_wait_fn=heartbeat_wait,
    )
    now = datetime(2026, 8, 18, 20, 16, tzinfo=SHANGHAI_TIMEZONE)
    outcome = {}

    def run_a():
        try:
            outcome["summary"] = runner_a.tick(now)
        except Exception as exc:
            outcome["error"] = exc

    worker = Thread(target=run_a)
    worker.start()
    assert service_a.entered.wait(timeout=3)
    assert heartbeat_wait.ready.wait(timeout=3)

    clock.set(200)
    heartbeat_wait.trigger.set()
    assert observed_repo.renewed.wait(timeout=3)
    assert runner_a._active_lock_guard.renewed.wait(timeout=3)
    clock.set(301)

    service_b = RecordingDataJobService(repo)
    runner_b = _runner(
        TradingRepository(settings.db_path),
        settings,
        owner="runner-b",
        data_job_service=service_b,
    )
    summary_b = runner_b.tick(now + timedelta(seconds=301))

    assert summary_b["lock_acquired"] is False
    assert summary_b["created"] == 0
    assert service_b.calls == []
    assert repo.get_job_by_key(
        "schedule:sync_pool_from_hotlist:2026-08-18"
    ) is None

    service_a.release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert "error" not in outcome
    assert outcome["summary"]["created"] == 2
    assert outcome["summary"]["lock_lost"] is False


def test_scheduler_renewal_loss_stops_after_inflight_executor_and_preserves_new_owner(
    setup,
):
    repo, settings = setup
    clock = AdjustableMonotonic()
    heartbeat_wait = ControlledHeartbeatWait()
    observed_repo = ObservedRenewalRepository(
        repo,
        "runner-a",
        transfer_on_renewal=True,
    )
    service = BlockingDataJobService()
    runner = SchedulerRunner(
        observed_repo,
        service,
        CalendarProvider(),
        settings,
        owner_id="runner-a",
        monotonic_fn=clock,
        heartbeat_wait_fn=heartbeat_wait,
    )
    now = datetime(2026, 8, 18, 20, 16, tzinfo=SHANGHAI_TIMEZONE)
    outcome = {}

    def run_a():
        try:
            outcome["summary"] = runner.tick(now)
        except Exception as exc:
            outcome["error"] = exc

    worker = Thread(target=run_a)
    worker.start()
    assert service.entered.wait(timeout=3)
    assert heartbeat_wait.ready.wait(timeout=3)

    clock.set(100)
    heartbeat_wait.trigger.set()
    assert observed_repo.transferred.wait(timeout=3)
    assert runner._active_lock_guard.lost.wait(timeout=3)
    service.release.set()
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert "error" not in outcome
    assert outcome["summary"]["lock_lost"] is True
    assert outcome["summary"]["blocked_reason"] == "SCHEDULER_LOCK_LOST"
    assert outcome["summary"]["created"] == 0
    assert len(service.calls) == 1
    assert observed_repo.release_owners[-1] == "runner-a"
    assert repo.get_job_by_key(
        "schedule:sync_pool_from_hotlist:2026-08-18"
    ) is None
    assert not repo.acquire_job_lock(
        lock_key=SCHEDULER_LOCK_KEY,
        owner_id="runner-c",
        now=now + timedelta(seconds=101),
        expires_at=now + timedelta(minutes=5),
    )


def test_scheduler_guard_fails_closed_when_heartbeat_misses_expiry(setup):
    repo, settings = setup
    clock = AdjustableMonotonic()
    heartbeat_wait = ControlledHeartbeatWait()
    service = BlockingDataJobService()
    runner = SchedulerRunner(
        repo,
        service,
        CalendarProvider(),
        settings,
        owner_id="runner-a",
        monotonic_fn=clock,
        heartbeat_wait_fn=heartbeat_wait,
    )
    now = datetime(2026, 8, 18, 20, 16, tzinfo=SHANGHAI_TIMEZONE)
    outcome = {}

    def run_a():
        outcome["summary"] = runner.tick(now)

    worker = Thread(target=run_a)
    worker.start()
    assert service.entered.wait(timeout=3)
    assert heartbeat_wait.ready.wait(timeout=3)

    clock.set(301)
    service.release.set()
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert outcome["summary"]["lock_lost"] is True
    assert outcome["summary"]["blocked_reason"] == "SCHEDULER_LOCK_LOST"
    assert outcome["summary"]["created"] == 0
    assert len(service.calls) == 1


def test_two_concurrent_runners_create_each_key_once(setup):
    repo, settings = setup
    repo.upsert_trade_calendar(
        [TradeDay(date(2026, 8, 18), True)], source="trusted"
    )
    now = datetime(2026, 8, 18, 23, 31, tzinfo=SHANGHAI_TIMEZONE)
    barrier = Barrier(2)

    def tick(owner):
        runner = _runner(
            TradingRepository(settings.db_path), settings, owner=owner
        )
        barrier.wait(timeout=3)
        return runner.tick(now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        summaries = list(executor.map(tick, ("runner-a", "runner-b")))

    assert sum(summary["created"] for summary in summaries) == 5
    for job_type in (
        "sync_pool_from_hotlist",
        "update_market_data",
        "reconcile_orders",
        "generate_daily_plan",
        "backup_database",
    ):
        assert repo.get_job_by_key(
            f"schedule:{job_type}:2026-08-18"
        ) is not None


def test_repeated_calendar_failure_records_one_issue_per_day(setup):
    repo, settings = setup
    runner = _runner(
        repo,
        settings,
        CalendarProvider(error=ProviderUnavailable("offline")),
    )
    now = datetime(2026, 10, 1, 20, 26, tzinfo=SHANGHAI_TIMEZONE)

    runner.tick(now)
    runner.tick(now + timedelta(seconds=30))

    issues = repo.list_data_issues(
        trade_date="2026-10-01", severity="BLOCKING"
    )
    assert [issue["issue_code"] for issue in issues] == [
        "CALENDAR_UNAVAILABLE"
    ]
