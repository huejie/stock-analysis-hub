"""Persistent trading-data job executor with execution-lease fencing."""

from datetime import date, datetime, timedelta
from threading import Event, Lock, Thread
from time import monotonic
from typing import Callable

from ..errors import (
    PlanBlockedError,
    PlanGenerationFailedError,
    PlanInProgressError,
    PlanSupersededError,
    TradingError,
)
from ..jobs.backup_database import run_backup
from ..providers.base import ProviderError, ProviderUnavailable
from .execution_service import ExecutionService
from .plan_service import PlanService
from .pool_service import PoolService
from .portfolio_service import PortfolioService


class LeaseLostError(RuntimeError):
    """Raised at cooperative fencing points after execution lease loss."""


class LeaseGuard:
    def __init__(
        self,
        *,
        expires_at: datetime,
        now_fn: Callable[[], datetime],
    ):
        self.lost = Event()
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

    def mark_lost(self, error: Exception) -> None:
        with self._lock:
            if self.lost.is_set():
                return
            self.last_error = error
            self.lost.set()

    def check(self) -> None:
        if not self.lost.is_set():
            return
        error = LeaseLostError("execution lease lost")
        if self.last_error is not None:
            raise error from self.last_error
        raise error


class DataJobService:
    """Claim and execute one queued job through a fenced terminal write."""

    def __init__(
        self,
        repo,
        market_data_service,
        settings,
        *,
        pool_service=None,
        execution_service=None,
        plan_service=None,
        backup_fn: Callable[[str], dict] = run_backup,
    ):
        self.repo = repo
        self.market_data_service = market_data_service
        self.settings = settings
        self.pool_service = pool_service or PoolService(repo)
        self.execution_service = execution_service or ExecutionService(repo)
        self.plan_service = plan_service or PlanService(
            repo,
            market_data_service,
            PortfolioService(repo),
        )
        self.backup_fn = backup_fn

    @staticmethod
    def _error_payload(exc: Exception) -> dict:
        if isinstance(exc, TradingError):
            return {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        if isinstance(exc, ProviderError):
            return {
                "code": "PROVIDER_UNAVAILABLE",
                "message": str(exc),
                "details": {
                    "provider": exc.provider,
                    "retriable": exc.retriable,
                },
            }
        if isinstance(exc, ProviderUnavailable):
            return {
                "code": "PROVIDER_UNAVAILABLE",
                "message": str(exc),
                "details": {},
            }
        return {
            "code": "JOB_EXECUTION_FAILED",
            "message": str(exc),
            "details": {"type": type(exc).__name__},
        }

    def run_next_queued_job(
        self, owner_id: str, now: datetime
    ) -> dict | None:
        claimed = self.repo.claim_next_job(owner_id=owner_id, now=now)
        if claimed is None:
            return None
        return self._run_claimed_job(
            claimed,
            owner_id=owner_id,
            now=now,
        )

    def _run_claimed_job(
        self, job: dict, *, owner_id: str, now: datetime
    ) -> dict:
        job_id = job["id"]
        expected_attempt = job["attempts"]
        ttl_seconds = float(
            getattr(self.settings, "trading_job_lock_ttl_seconds", 300)
        )
        try:
            leased_job = self.repo.acquire_job_execution_lease(
                job_id,
                owner_id=owner_id,
                expected_attempt=expected_attempt,
                now=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
        except Exception:
            # Without the execution lease this worker owns no terminal write.
            return self.repo.get_job(job_id)
        if leased_job is None:
            return self.repo.get_job(job_id)
        # claim_next_job clears error_json before the execution lease is
        # acquired. Preserve the prior-attempt classification carried by the
        # claimed snapshot so PLAN_IN_PROGRESS retries do not fork a new run.
        leased_job["previous_error"] = job.get("previous_error")

        monotonic_started = monotonic()

        def lease_now() -> datetime:
            return now + timedelta(seconds=monotonic() - monotonic_started)

        guard = LeaseGuard(
            expires_at=now + timedelta(seconds=ttl_seconds),
            now_fn=lease_now,
        )
        stop_heartbeat, heartbeat = self._start_lease_heartbeat(
            job_id=job_id,
            owner_id=owner_id,
            expected_attempt=expected_attempt,
            ttl_seconds=ttl_seconds,
            guard=guard,
        )
        try:
            try:
                guard.check()
                result = self._dispatch(leased_job, guard)
                guard.check()
            except LeaseLostError:
                pass
            except PlanInProgressError as exc:
                # Another live plan owner is not a failed generation attempt.
                # Leave this job RUNNING without its execution lease; the
                # Scheduler reaper observes the plan-generation lease and
                # requeues only after it has expired or been released.
                try:
                    guard.check()
                    self.repo.wait_for_plan_generation(
                        job_id,
                        error=self._error_payload(exc),
                        owner_id=owner_id,
                        expected_attempt=expected_attempt,
                        now=guard.now(),
                    )
                except LeaseLostError:
                    pass
            except Exception as exc:
                try:
                    guard.check()
                except LeaseLostError:
                    pass
                else:
                    self._retry_attempt(
                        job_id,
                        exc=exc,
                        owner_id=owner_id,
                        expected_attempt=expected_attempt,
                        now=guard.now(),
                    )
            else:
                try:
                    self.repo.complete_job(
                        job_id,
                        result=result,
                        owner_id=owner_id,
                        expected_attempt=expected_attempt,
                        now=guard.now(),
                    )
                except ValueError as exc:
                    guard.mark_lost(exc)
        finally:
            stop_heartbeat.set()
            heartbeat.join()
            self.repo.release_job_execution_lease(
                job_id,
                owner_id=owner_id,
                expected_attempt=expected_attempt,
            )
        return self.repo.get_job(job_id)

    def _retry_attempt(
        self,
        job_id: int,
        *,
        exc: Exception,
        owner_id: str,
        expected_attempt: int,
        now: datetime,
    ) -> None:
        try:
            self.repo.retry_or_fail_job(
                job_id,
                error=self._error_payload(exc),
                owner_id=owner_id,
                expected_attempt=expected_attempt,
                now=now,
                max_attempts=3,
            )
        except ValueError:
            # The lease or attempt changed; the old worker is fenced out.
            pass

    def _start_lease_heartbeat(
        self,
        *,
        job_id: int,
        owner_id: str,
        expected_attempt: int,
        ttl_seconds: float,
        guard: LeaseGuard,
    ) -> tuple[Event, Thread]:
        stop = Event()
        interval = max(0.01, min(ttl_seconds / 3, 30.0))
        max_backoff = max(0.005, min(interval, 0.25))

        def heartbeat_loop() -> None:
            while not stop.wait(interval):
                backoff = min(0.01, max_backoff)
                last_error: Exception | None = None
                while not stop.is_set():
                    heartbeat_now = guard.now()
                    remaining = (
                        guard.expires_at - heartbeat_now
                    ).total_seconds()
                    if remaining <= 0:
                        guard.mark_lost(
                            last_error
                            or TimeoutError("execution lease expired")
                        )
                        return
                    expires_at = heartbeat_now + timedelta(
                        seconds=ttl_seconds
                    )
                    try:
                        renewed = self.repo.renew_job_execution_lease(
                            job_id,
                            owner_id=owner_id,
                            expected_attempt=expected_attempt,
                            now=heartbeat_now,
                            expires_at=expires_at,
                        )
                        if stop.is_set():
                            return
                        if not renewed:
                            renewed = (
                                self.repo
                                .renew_or_reacquire_job_execution_lease(
                                    job_id,
                                    owner_id=owner_id,
                                    expected_attempt=expected_attempt,
                                    now=heartbeat_now,
                                    expires_at=expires_at,
                                )
                            )
                        if stop.is_set():
                            return
                        if not renewed:
                            guard.mark_lost(
                                RuntimeError(
                                    "execution lease owner or attempt changed"
                                )
                            )
                            return
                    except Exception as exc:
                        last_error = exc
                        delay = min(backoff, remaining)
                        if stop.wait(delay):
                            return
                        backoff = min(backoff * 2, max_backoff)
                        continue
                    guard.renewed_until(expires_at)
                    break

        heartbeat = Thread(
            target=heartbeat_loop,
            name=f"trade-job-heartbeat-{job_id}-{expected_attempt}",
            daemon=True,
        )
        heartbeat.start()
        return stop, heartbeat

    @staticmethod
    def _as_date(raw: str | None, field: str) -> date:
        if not raw:
            raise ValueError(f"{field} 不能为空")
        return date.fromisoformat(raw)

    def _benchmark_codes(self) -> list[str]:
        return [
            code.strip()
            for code in self.settings.trading_benchmark_codes.split(",")
            if code.strip()
        ]

    def _codes_or_pool(self, request: dict) -> list[str]:
        codes = request.get("stock_codes")
        if codes is not None:
            return codes
        return self.repo.get_latest_pool_codes("default")

    def _dispatch(self, job: dict, guard: LeaseGuard) -> dict:
        request = job["request"]
        job_type = job["job_type"]
        guard.check()

        if job_type == "update_bars":
            codes = self._codes_or_pool(request)
            if not codes:
                return {"rows_written": 0, "skipped_reason": "NO_CODES"}
            count = self.market_data_service.update_bars(
                codes,
                self._as_date(request.get("trade_date"), "trade_date"),
            )
            guard.check()
            return {"rows_written": count}

        if job_type == "backfill_bars":
            codes = self._codes_or_pool(request)
            if not codes:
                return {"rows_written": 0, "skipped_reason": "NO_CODES"}
            count = self.market_data_service.update_bars_range(
                codes,
                self._as_date(request.get("start_date"), "start_date"),
                self._as_date(request.get("end_date"), "end_date"),
            )
            guard.check()
            return {"rows_written": count}

        if job_type == "refresh_calendar":
            count = self.market_data_service.refresh_trade_calendar(
                self._as_date(request.get("start_date"), "start_date"),
                self._as_date(request.get("end_date"), "end_date"),
            )
            guard.check()
            return {"calendar_days": count}

        if job_type == "validate_data":
            report = self.market_data_service.check_data_health(
                trade_date=self._as_date(
                    request.get("trade_date"), "trade_date"
                ),
                pool_name="default",
                benchmark_codes=self._benchmark_codes(),
            )
            guard.check()
            return {"health": report}

        return self._dispatch_internal(
            job_type,
            request,
            guard,
            job_attempt=job["attempts"],
            previous_error=job.get("previous_error"),
        )

    def _dispatch_internal(
        self,
        job_type: str,
        request: dict,
        guard: LeaseGuard,
        *,
        job_attempt: int,
        previous_error: dict | None,
    ) -> dict:
        target = self._as_date(request.get("trade_date"), "trade_date")
        guard.check()

        if job_type == "sync_pool_from_hotlist":
            pool = self.pool_service.sync_from_hotlist(
                request.get("pool_name", "default"),
                top_n=int(request.get("top_n", 10)),
            )
            guard.check()
            return {"outcome": "COMPLETED", "pool": pool}

        if job_type == "update_market_data":
            codes = set(self.repo.get_latest_pool_codes("default"))
            guard.check()
            for account in self.repo.list_accounts(active_only=True):
                guard.check()
                codes.update(
                    position["stock_code"]
                    for position in self.repo.get_positions(account["id"])
                    if position["quantity"] > 0
                )
            stock_codes = sorted(codes)
            guard.check()
            pool_rows = (
                self.market_data_service.update_bars(stock_codes, target)
                if stock_codes
                else 0
            )
            guard.check()
            benchmark_rows = self.market_data_service.update_benchmark(
                target, self._benchmark_codes()
            )
            guard.check()
            return {
                "outcome": "COMPLETED",
                "pool_rows": pool_rows,
                "benchmark_rows": benchmark_rows,
                "stock_codes": stock_codes,
            }

        if job_type == "reconcile_orders":
            result = self.execution_service.reconcile_orders(
                target, check_fence=guard.check
            )
            guard.check()
            return {"outcome": "COMPLETED", **result}

        if job_type == "generate_daily_plan":
            accounts = self.repo.list_accounts(active_only=True)
            guard.check()
            if not accounts:
                return {
                    "outcome": "SKIPPED",
                    "reason": "NO_ACTIVE_ACCOUNT",
                    "plans": [],
                }
            strategy = self.repo.get_active_strategy("default")
            guard.check()
            if not strategy:
                return {
                    "outcome": "SKIPPED",
                    "reason": "NO_ACTIVE_STRATEGY",
                    "plans": [],
                }
            pools = self.repo.list_stock_pool_versions(
                "default", usable_only=True
            )
            guard.check()
            if not pools:
                return {
                    "outcome": "SKIPPED",
                    "reason": "NO_USABLE_POOL",
                    "plans": [],
                }
            plans = []
            force_new_version = (
                job_attempt > 1
                and (previous_error or {}).get("code") != "PLAN_IN_PROGRESS"
            )
            for account in accounts:
                guard.check()
                plan_kwargs = {
                    "account_id": account["id"],
                    "signal_date": target,
                    "stock_pool_version_id": pools[0]["id"],
                    "strategy_version_id": strategy["id"],
                }
                if force_new_version:
                    plan_kwargs["force_new_version"] = True
                plan = self.plan_service.generate_plan(**plan_kwargs)
                if plan["status"] in ("CREATED", "VALIDATING", "GENERATING"):
                    raise PlanInProgressError(
                        f"计划 {plan['id']} 正由另一执行者生成",
                        details={
                            "run_id": plan["id"],
                            "status": plan["status"],
                        },
                    )
                if plan["status"] == "FAILED":
                    raise PlanGenerationFailedError(
                        f"计划 {plan['id']} 生成失败",
                        details={"run_id": plan["id"]},
                    )
                if plan["status"] == "BLOCKED":
                    raise PlanBlockedError(
                        f"计划 {plan['id']} 被数据门禁阻断",
                        details={"run_id": plan["id"]},
                    )
                if plan["status"] == "SUPERSEDED":
                    raise PlanSupersededError(
                        f"计划 {plan['id']} 已被替代",
                        details={"run_id": plan["id"]},
                    )
                if plan["status"] not in ("READY", "PARTIAL", "PUBLISHED"):
                    raise RuntimeError(
                        f"计划 {plan['id']} 返回未知状态 {plan['status']}"
                    )
                plans.append(plan)
                guard.check()
            return {"outcome": "COMPLETED", "plans": plans}

        if job_type == "backup_database":
            backup = self.backup_fn(self.settings.db_path)
            guard.check()
            if (
                not isinstance(backup, dict)
                or not backup.get("path")
                or not backup.get("integrity_ok", False)
            ):
                raise RuntimeError("数据库备份或完整性检查失败")
            return {"outcome": "COMPLETED", "backup": backup}

        raise ValueError(f"不支持的 job_type: {job_type}")
