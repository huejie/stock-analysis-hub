"""Testable Shanghai-time scheduler decisions backed by persistent jobs."""

from datetime import datetime, time, timedelta
from threading import Event, Lock, Thread
from time import monotonic
from typing import Callable, TypedDict
from uuid import uuid4
from zoneinfo import ZoneInfo

from ..providers.base import ProviderError, ProviderUnavailable


SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
SCHEDULE = (
    (time(20, 10), "sync_pool_from_hotlist", True),
    (time(20, 15), "update_market_data", True),
    (time(20, 20), "reconcile_orders", True),
    (time(20, 25), "generate_daily_plan", True),
    (time(23, 30), "backup_database", False),
)
SCHEDULER_LOCK_KEY = "scheduler:tick"


class SchedulerSummary(TypedDict):
    requeued: int
    created: int
    attempted: int
    succeeded: int
    retried: int
    failed: int
    calendar_status: str
    blocked_reason: str | None
    lock_acquired: bool
    lock_lost: bool


class SchedulerLockLostError(RuntimeError):
    """Raised at cooperative fences after scheduler-lock loss."""


class SchedulerLockGuard:
    def __init__(
        self,
        *,
        expires_at: datetime,
        now_fn: Callable[[], datetime],
    ):
        self.lost = Event()
        self.renewed = Event()
        self.last_error: Exception | None = None
        self._expires_at = expires_at
        self._now_fn = now_fn
        self._lock = Lock()

    def now(self) -> datetime:
        return self._now_fn()

    @property
    def expires_at(self) -> datetime:
        with self._lock:
            return self._expires_at

    def renewed_until(self, expires_at: datetime) -> None:
        with self._lock:
            self._expires_at = expires_at
        self.renewed.set()

    def mark_lost(self, error: Exception) -> None:
        with self._lock:
            if self.lost.is_set():
                return
            self.last_error = error
            self.lost.set()

    def check(self) -> None:
        if not self.lost.is_set() and self.now() >= self.expires_at:
            self.mark_lost(
                TimeoutError("scheduler lock lease expired")
            )
        if not self.lost.is_set():
            return
        error = SchedulerLockLostError("scheduler lock lost")
        if self.last_error is not None:
            raise error from self.last_error
        raise error


class SchedulerRunner:
    def __init__(
        self,
        repo,
        data_job_service,
        provider,
        settings,
        *,
        owner_id: str | None = None,
        monotonic_fn: Callable[[], float] = monotonic,
        heartbeat_wait_fn: Callable[[Event, float], bool] | None = None,
    ):
        self.repo = repo
        self.data_job_service = data_job_service
        self.provider = provider
        self.settings = settings
        self.owner_id = owner_id or f"scheduler-{uuid4().hex[:12]}"
        self.timezone = SHANGHAI_TIMEZONE
        self._monotonic_fn = monotonic_fn
        self._heartbeat_wait_fn = (
            heartbeat_wait_fn
            if heartbeat_wait_fn is not None
            else lambda stop, seconds: stop.wait(seconds)
        )
        self._active_lock_guard: SchedulerLockGuard | None = None

    def _local_now(self, now: datetime | None) -> datetime:
        if now is None:
            return datetime.now(self.timezone)
        if now.tzinfo is None:
            return now.replace(tzinfo=self.timezone)
        return now.astimezone(self.timezone)

    @staticmethod
    def _empty_summary() -> SchedulerSummary:
        return {
            "requeued": 0,
            "created": 0,
            "attempted": 0,
            "succeeded": 0,
            "retried": 0,
            "failed": 0,
            "calendar_status": "not_checked",
            "blocked_reason": None,
            "lock_acquired": False,
            "lock_lost": False,
        }

    def _run_one(
        self,
        now: datetime,
        summary: SchedulerSummary,
        guard: SchedulerLockGuard,
    ) -> dict | None:
        guard.check()
        job = self.data_job_service.run_next_queued_job(
            owner_id=self.owner_id,
            now=now,
        )
        guard.check()
        if job is None:
            return None
        summary["attempted"] += 1
        if job["status"] == "SUCCEEDED":
            summary["succeeded"] += 1
        elif job["status"] == "QUEUED":
            summary["retried"] += 1
        elif job["status"] == "FAILED":
            summary["failed"] += 1
        return job

    def _record_calendar_unavailable(
        self,
        target_date,
        message: str,
        *,
        details: dict | None = None,
        guard: SchedulerLockGuard,
    ) -> None:
        guard.check()
        existing = self.repo.list_data_issues(
            trade_date=target_date.isoformat(),
            severity="BLOCKING",
        )
        guard.check()
        if any(
            issue["issue_code"] == "CALENDAR_UNAVAILABLE"
            and issue["source"] == "scheduler"
            for issue in existing
        ):
            return
        guard.check()
        self.repo.create_data_issue(
            severity="BLOCKING",
            issue_code="CALENDAR_UNAVAILABLE",
            message=message,
            trade_date=target_date.isoformat(),
            source="scheduler",
            details=details or {},
        )

    def _calendar_status(
        self, target_date, guard: SchedulerLockGuard
    ) -> str:
        guard.check()
        cached = self.repo.get_trade_day(target_date)
        guard.check()
        if cached is not None:
            return "open" if cached.is_open else "closed"

        try:
            get_with_source = getattr(
                self.provider, "get_trade_calendar_with_source", None
            )
            if callable(get_with_source):
                days, source = get_with_source(target_date, target_date)
            else:
                days = self.provider.get_trade_calendar(
                    target_date, target_date
                )
                source = getattr(self.provider, "name", "composite")
            guard.check()
        except (ProviderError, ProviderUnavailable) as exc:
            self._record_calendar_unavailable(
                target_date,
                f"{target_date.isoformat()} 交易日历不可用: {exc}",
                guard=guard,
            )
            return "unavailable"

        if len(days) != 1 or days[0].date != target_date:
            self._record_calendar_unavailable(
                target_date,
                f"{target_date.isoformat()} 交易日历响应不完整",
                details={"rows": len(days)},
                guard=guard,
            )
            return "unavailable"

        guard.check()
        self.repo.upsert_trade_calendar(days, source=source)
        guard.check()
        return "open" if days[0].is_open else "closed"

    def _create_due_jobs(
        self,
        now: datetime,
        calendar_status: str,
        summary: SchedulerSummary,
        guard: SchedulerLockGuard,
    ) -> None:
        local_time = now.time().replace(tzinfo=None)
        for run_time, job_type, requires_trade_day in SCHEDULE:
            guard.check()
            if run_time > local_time:
                continue
            if requires_trade_day and calendar_status != "open":
                continue
            job_key = (
                f"schedule:{job_type}:{now.date().isoformat()}"
            )
            guard.check()
            if self.repo.get_job_by_key(job_key) is not None:
                continue
            guard.check()
            self.repo.create_job(
                job_type,
                job_key,
                {
                    "trade_date": now.date().isoformat(),
                    "scheduled_for": datetime.combine(
                        now.date(), run_time, tzinfo=self.timezone
                    ).isoformat(),
                },
            )
            guard.check()
            summary["created"] += 1

    def _tick_locked(
        self,
        local_now: datetime,
        summary: SchedulerSummary,
        guard: SchedulerLockGuard,
    ) -> None:
        stale_seconds = float(
            getattr(self.settings, "trading_stale_job_seconds", 1800)
        )
        guard.check()
        summary["requeued"] = self.repo.requeue_stale_jobs(
            stale_before=local_now - timedelta(seconds=stale_seconds),
            now=local_now,
        )
        guard.check()

        first = self._run_one(local_now, summary, guard)
        if not getattr(
            self.settings, "trading_schedule_enabled", False
        ):
            return

        local_time = local_now.time().replace(tzinfo=None)
        if any(
            local_time >= run_time and requires_trade_day
            for run_time, _, requires_trade_day in SCHEDULE
        ):
            guard.check()
            summary["calendar_status"] = self._calendar_status(
                local_now.date(), guard
            )
            if summary["calendar_status"] == "unavailable":
                summary["blocked_reason"] = "CALENDAR_UNAVAILABLE"

        self._create_due_jobs(
            local_now,
            summary["calendar_status"],
            summary,
            guard,
        )

        if summary["created"] and (
            first is None or first["status"] == "SUCCEEDED"
        ):
            self._run_one(local_now, summary, guard)

    def _start_lock_heartbeat(
        self,
        *,
        ttl_seconds: float,
        guard: SchedulerLockGuard,
    ) -> tuple[Event, Thread]:
        stop = Event()
        interval = max(0.01, ttl_seconds / 3)

        def heartbeat_loop() -> None:
            while not self._heartbeat_wait_fn(stop, interval):
                heartbeat_now = guard.now()
                if heartbeat_now >= guard.expires_at:
                    guard.mark_lost(
                        TimeoutError("scheduler lock lease expired")
                    )
                    return
                expires_at = heartbeat_now + timedelta(
                    seconds=ttl_seconds
                )
                try:
                    renewed = self.repo.acquire_job_lock(
                        lock_key=SCHEDULER_LOCK_KEY,
                        owner_id=self.owner_id,
                        now=heartbeat_now,
                        expires_at=expires_at,
                    )
                except Exception as exc:
                    guard.mark_lost(exc)
                    return
                if not renewed:
                    guard.mark_lost(
                        RuntimeError(
                            "scheduler lock owner changed during renewal"
                        )
                    )
                    return
                guard.renewed_until(expires_at)

        heartbeat = Thread(
            target=heartbeat_loop,
            name=f"scheduler-lock-heartbeat-{self.owner_id}",
            daemon=True,
        )
        heartbeat.start()
        return stop, heartbeat

    def tick(self, now: datetime | None = None) -> SchedulerSummary:
        local_now = self._local_now(now)
        summary = self._empty_summary()
        lock_seconds = float(
            getattr(self.settings, "trading_job_lock_ttl_seconds", 300)
        )
        acquired = self.repo.acquire_job_lock(
            lock_key=SCHEDULER_LOCK_KEY,
            owner_id=self.owner_id,
            now=local_now,
            expires_at=local_now + timedelta(seconds=lock_seconds),
        )
        if not acquired:
            return summary

        summary["lock_acquired"] = True
        monotonic_started = self._monotonic_fn()

        def lock_now() -> datetime:
            return local_now + timedelta(
                seconds=self._monotonic_fn() - monotonic_started
            )

        guard = SchedulerLockGuard(
            expires_at=local_now + timedelta(seconds=lock_seconds),
            now_fn=lock_now,
        )
        self._active_lock_guard = guard
        stop_heartbeat, heartbeat = self._start_lock_heartbeat(
            ttl_seconds=lock_seconds,
            guard=guard,
        )
        try:
            try:
                self._tick_locked(local_now, summary, guard)
            except SchedulerLockLostError:
                summary["lock_lost"] = True
                summary["blocked_reason"] = "SCHEDULER_LOCK_LOST"
            return summary
        finally:
            stop_heartbeat.set()
            heartbeat.join()
            self.repo.release_job_lock(
                lock_key=SCHEDULER_LOCK_KEY,
                owner_id=self.owner_id,
            )
