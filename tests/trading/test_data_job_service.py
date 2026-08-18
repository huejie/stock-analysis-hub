from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import json
import sqlite3
import time

import pytest

from backend.trading.domain import DailyBar
from backend.trading.errors import EmptyProviderResultError
from backend.trading.migrations import run_migrations
from backend.trading.repository import TradingRepository
from backend.trading.services.data_job_service import (
    DataJobService,
    LeaseLostError,
)
from backend.trading.services.execution_service import ExecutionService
from backend.trading.services.pool_service import PoolService


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
TRADE_DATE = date(2026, 8, 19)


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "data-jobs.db")
    run_migrations(db_path)
    return TradingRepository(db_path)


@pytest.fixture
def settings(repo):
    return SimpleNamespace(
        db_path=repo.db_path,
        trading_benchmark_codes="000300.SH,000905.SH",
        trading_job_lock_ttl_seconds=30,
    )


class FakeMarketDataService:
    def __init__(self, *, fail=False, delay=0):
        self.fail = fail
        self.delay = delay
        self.calls = []

    def update_bars(self, codes, target_date):
        self.calls.append(("update_bars", list(codes), target_date))
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise EmptyProviderResultError(
                "行情为空", details={"codes": list(codes)}
            )
        return len(codes)

    def update_benchmark(self, target_date, benchmark_codes):
        self.calls.append(
            ("update_benchmark", target_date, list(benchmark_codes))
        )
        return len(benchmark_codes)


class RecordingRepository:
    def __init__(self, repo):
        self.repo = repo
        self.claim_calls = []
        self.completed = []
        self.retried = []

    def __getattr__(self, name):
        return getattr(self.repo, name)

    def claim_next_job(self, *, owner_id, now, max_attempts=3):
        self.claim_calls.append((owner_id, now))
        return self.repo.claim_next_job(
            owner_id=owner_id, now=now, max_attempts=max_attempts
        )

    def complete_job(
        self, job_id, *, result, owner_id, expected_attempt, now
    ):
        self.completed.append((job_id, owner_id, expected_attempt))
        return self.repo.complete_job(
            job_id,
            result=result,
            owner_id=owner_id,
            expected_attempt=expected_attempt,
            now=now,
        )

    def retry_or_fail_job(
        self,
        job_id,
        *,
        error,
        owner_id,
        expected_attempt,
        now,
        max_attempts=3,
    ):
        self.retried.append((job_id, owner_id, expected_attempt, error))
        return self.repo.retry_or_fail_job(
            job_id,
            error=error,
            owner_id=owner_id,
            expected_attempt=expected_attempt,
            now=now,
            max_attempts=max_attempts,
        )


class LostLeaseRepository(RecordingRepository):
    def renew_job_execution_lease(self, *args, **kwargs):
        return False

    def renew_or_reacquire_job_execution_lease(self, *args, **kwargs):
        return False


class HotlistRepository(RecordingRepository):
    def get_latest_hotlist_top(self, limit=10):
        return [
            {"stock_code": "000001", "stock_name": "平安银行", "rank": 1},
            {"stock_code": "600000", "stock_name": "浦发银行", "rank": 2},
        ][:limit]


class FakePlanService:
    def __init__(self):
        self.calls = []

    def generate_plan(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "id": len(self.calls),
            "status": "READY",
            "reused": False,
        }


class InProgressPlanService(FakePlanService):
    def generate_plan(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": 41, "status": "GENERATING", "reused": True}


class StaticStatusPlanService(FakePlanService):
    def __init__(self, status):
        super().__init__()
        self.status = status

    def generate_plan(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": 42, "status": self.status, "reused": True}


def _create_pool(repo, code="000001.SZ", name="平安银行"):
    return repo.create_stock_pool_version(
        "default", [{"stock_code": code, "stock_name": name}]
    )


def _create_active_strategy(repo):
    strategy = repo.create_strategy_version(
        strategy_code="default", name="默认策略", params_json={}
    )
    repo.activate_strategy(strategy["id"])
    return repo.get_strategy(strategy["id"])


def _run(service, repo, job_type, key, request=None, *, now=NOW):
    job_id = repo.create_job(
        job_type,
        key,
        request or {"trade_date": TRADE_DATE.isoformat()},
    )
    result = service.run_next_queued_job(owner_id="worker-a", now=now)
    return job_id, result


def test_run_next_claims_once_and_completes_with_owner_attempt_fence(
    repo, settings
):
    _create_pool(repo)
    recording_repo = RecordingRepository(repo)
    market_data = FakeMarketDataService()
    service = DataJobService(recording_repo, market_data, settings)

    job_id, result = _run(
        service, recording_repo, "update_market_data", "update-success"
    )

    assert len(recording_repo.claim_calls) == 1
    assert recording_repo.completed == [(job_id, "worker-a", 1)]
    assert result["status"] == "SUCCEEDED"
    assert result["result"] == {
        "outcome": "COMPLETED",
        "pool_rows": 1,
        "benchmark_rows": 2,
        "stock_codes": ["000001.SZ"],
    }


def test_update_market_data_updates_pool_positions_and_all_benchmarks(
    repo, settings
):
    _create_pool(repo)
    account = repo.create_account(
        name="main", initial_equity=100_000, cash_balance=100_000
    )
    repo.upsert_position(
        account_id=account["id"],
        stock_code="600000.SH",
        quantity=100,
        available_quantity=100,
        average_cost=10,
    )
    market_data = FakeMarketDataService()
    service = DataJobService(repo, market_data, settings)

    _, result = _run(
        service, repo, "update_market_data", "update-pool-and-position"
    )

    assert result["status"] == "SUCCEEDED"
    assert market_data.calls == [
        (
            "update_bars",
            ["000001.SZ", "600000.SH"],
            TRADE_DATE,
        ),
        (
            "update_benchmark",
            TRADE_DATE,
            ["000300.SH", "000905.SH"],
        ),
    ]


def test_provider_failure_retries_twice_then_fails_on_third_attempt(
    repo, settings
):
    _create_pool(repo)
    recording_repo = RecordingRepository(repo)
    service = DataJobService(
        recording_repo, FakeMarketDataService(fail=True), settings
    )
    job_id = recording_repo.create_job(
        "update_market_data",
        "provider-retry-three",
        {"trade_date": TRADE_DATE.isoformat()},
    )

    statuses = []
    for offset in range(3):
        result = service.run_next_queued_job(
            owner_id="worker-a", now=NOW + timedelta(minutes=offset)
        )
        statuses.append(result["status"])

    assert statuses == ["QUEUED", "QUEUED", "FAILED"]
    assert [call[2] for call in recording_repo.retried] == [1, 2, 3]
    final = recording_repo.get_job(job_id)
    assert final["attempts"] == 3
    assert final["error"]["code"] == "EMPTY_PROVIDER_RESULT"
    assert final["error"]["details"] == {"codes": ["000001.SZ"]}


def test_lost_lease_stops_remaining_stages_and_never_completes(repo, settings, monkeypatch):
    _create_pool(repo)
    lost_repo = LostLeaseRepository(repo)
    market_data = FakeMarketDataService()
    service = DataJobService(lost_repo, market_data, settings)
    captured = {}

    def controlled_heartbeat(**kwargs):
        from threading import Event, Thread

        captured["guard"] = kwargs["guard"]
        stop = Event()
        heartbeat = Thread(target=stop.wait)
        heartbeat.start()
        return stop, heartbeat

    original_update = market_data.update_bars

    def lose_lease_during_first_stage(codes, target_date):
        result = original_update(codes, target_date)
        captured["guard"].mark_lost(RuntimeError("controlled owner loss"))
        return result

    monkeypatch.setattr(service, "_start_lease_heartbeat", controlled_heartbeat)
    monkeypatch.setattr(market_data, "update_bars", lose_lease_during_first_stage)

    job_id, result = _run(
        service, lost_repo, "update_market_data", "lease-lost"
    )

    assert result["status"] == "RUNNING"
    assert lost_repo.completed == []
    assert lost_repo.retried == []
    assert market_data.calls == [
        ("update_bars", ["000001.SZ"], TRADE_DATE)
    ]
    assert repo.get_job(job_id)["result"] is None


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("no_account", "NO_ACTIVE_ACCOUNT"),
        ("no_strategy", "NO_ACTIVE_STRATEGY"),
        ("no_usable_pool", "NO_USABLE_POOL"),
    ],
)
def test_generate_plan_explicitly_skips_missing_runtime_inputs(
    repo, settings, state, reason
):
    if state != "no_account":
        repo.create_account(
            name="main", initial_equity=100_000, cash_balance=100_000
        )
    if state == "no_usable_pool":
        _create_active_strategy(repo)
        pool = _create_pool(repo)
        repo.mark_stock_pool_unusable(pool["id"], "test")
    service = DataJobService(repo, FakeMarketDataService(), settings)

    _, result = _run(
        service, repo, "generate_daily_plan", f"skip-{state}"
    )

    assert result["status"] == "SUCCEEDED"
    assert result["result"] == {
        "outcome": "SKIPPED",
        "reason": reason,
        "plans": [],
    }


def test_generate_plan_uses_latest_usable_pool_and_current_plan_service(
    repo, settings
):
    account = repo.create_account(
        name="main", initial_equity=100_000, cash_balance=100_000
    )
    strategy = _create_active_strategy(repo)
    usable_pool = _create_pool(repo)
    invalid_pool = _create_pool(repo, "600000.SH", "浦发银行")
    repo.mark_stock_pool_unusable(invalid_pool["id"], "polluted")
    plan_service = FakePlanService()
    service = DataJobService(
        repo,
        FakeMarketDataService(),
        settings,
        plan_service=plan_service,
    )

    _, result = _run(
        service, repo, "generate_daily_plan", "generate-usable"
    )

    assert result["status"] == "SUCCEEDED"
    assert result["result"]["outcome"] == "COMPLETED"
    assert result["result"]["plans"][0]["status"] == "READY"
    assert plan_service.calls == [
        {
            "account_id": account["id"],
            "signal_date": TRADE_DATE,
            "stock_pool_version_id": usable_pool["id"],
            "strategy_version_id": strategy["id"],
        }
    ]


def test_generate_plan_in_progress_requeues_job_instead_of_completing(
    repo, settings
):
    repo.create_account(
        name="main", initial_equity=100_000, cash_balance=100_000
    )
    _create_active_strategy(repo)
    _create_pool(repo)
    recording_repo = RecordingRepository(repo)
    plan_service = InProgressPlanService()
    service = DataJobService(
        recording_repo,
        FakeMarketDataService(),
        settings,
        plan_service=plan_service,
    )

    job_id, result = _run(
        service, recording_repo, "generate_daily_plan", "generate-in-progress"
    )

    assert result["status"] == "RUNNING"
    assert result["result"] is None
    assert result["error"]["code"] == "PLAN_IN_PROGRESS"
    assert recording_repo.completed == []
    assert recording_repo.retried == []


def test_generate_plan_in_progress_retry_does_not_force_new_version(
    repo, settings
):
    repo.create_account(
        name="main", initial_equity=100_000, cash_balance=100_000
    )
    _create_active_strategy(repo)
    _create_pool(repo)
    plan_service = InProgressPlanService()
    service = DataJobService(
        repo,
        FakeMarketDataService(),
        settings,
        plan_service=plan_service,
    )
    repo.create_job(
        "generate_daily_plan",
        "generate-in-progress-retry",
        {"trade_date": TRADE_DATE.isoformat()},
    )

    first = service.run_next_queued_job(owner_id="worker-a", now=NOW)
    second = service.run_next_queued_job(
        owner_id="worker-a", now=NOW + timedelta(minutes=1)
    )
    assert repo.requeue_stale_jobs(
        stale_before=NOW + timedelta(minutes=1),
        now=NOW + timedelta(minutes=1),
    ) == 1
    third = service.run_next_queued_job(
        owner_id="worker-a", now=NOW + timedelta(minutes=1)
    )

    assert first["status"] == "RUNNING"
    assert second is None
    assert third["status"] == "RUNNING"
    assert plan_service.calls == [
        {
            "account_id": 1,
            "signal_date": TRADE_DATE,
            "stock_pool_version_id": 1,
            "strategy_version_id": 1,
        },
        {
            "account_id": 1,
            "signal_date": TRADE_DATE,
            "stock_pool_version_id": 1,
            "strategy_version_id": 1,
        },
    ]


@pytest.mark.parametrize("status", ["READY", "PARTIAL", "PUBLISHED"])
def test_generate_plan_success_state_matrix_completes_job(
    repo, settings, status
):
    repo.create_account(
        name="main", initial_equity=100_000, cash_balance=100_000
    )
    _create_active_strategy(repo)
    _create_pool(repo)
    service = DataJobService(
        repo,
        FakeMarketDataService(),
        settings,
        plan_service=StaticStatusPlanService(status),
    )

    _, result = _run(
        service, repo, "generate_daily_plan", f"generate-success-{status}"
    )

    assert result["status"] == "SUCCEEDED"
    assert result["result"]["plans"][0]["status"] == status


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        ("BLOCKED", "PLAN_BLOCKED"),
        ("FAILED", "PLAN_GENERATION_FAILED"),
        ("SUPERSEDED", "PLAN_SUPERSEDED"),
    ],
)
def test_generate_plan_non_success_terminal_matrix_retries_job(
    repo, settings, status, error_code
):
    repo.create_account(
        name="main", initial_equity=100_000, cash_balance=100_000
    )
    _create_active_strategy(repo)
    _create_pool(repo)
    service = DataJobService(
        repo,
        FakeMarketDataService(),
        settings,
        plan_service=StaticStatusPlanService(status),
    )

    _, result = _run(
        service, repo, "generate_daily_plan", f"generate-terminal-{status}"
    )

    assert result["status"] == "QUEUED"
    assert result["error"]["code"] == error_code


def _add_plan_bars(repo):
    for code, start in (
        ("000300.SH", 3000.0),
        ("000905.SH", 5000.0),
        ("000001.SZ", 10.0),
    ):
        bars = []
        for offset in range(70):
            trade_date = TRADE_DATE - timedelta(days=69 - offset)
            close = start * (1.003 ** offset)
            bars.append(DailyBar(
                code=code,
                trade_date=trade_date,
                open=close * 0.999,
                high=close * 1.02,
                low=close * 0.98,
                close=close,
                volume=1_000_000,
                source="test",
            ))
        repo.upsert_daily_bars(bars)


def test_failed_plan_job_retry_forces_new_run_and_preserves_old_history(
    repo, settings, monkeypatch
):
    account = repo.create_account(
        name="main", initial_equity=100_000, cash_balance=100_000
    )
    strategy = _create_active_strategy(repo)
    pool = _create_pool(repo)
    _add_plan_bars(repo)
    from backend.trading.services.market_data_service import MarketDataService
    from backend.trading.services.plan_service import PlanService
    from backend.trading.services.portfolio_service import PortfolioService

    real_service = PlanService(
        repo,
        MarketDataService(repo, provider=None),
        PortfolioService(repo),
        benchmark_codes=["000300.SH", "000905.SH"],
    )
    original_generate_items = real_service._generate_items
    generation_calls = 0

    def fail_once(**kwargs):
        nonlocal generation_calls
        generation_calls += 1
        if generation_calls == 1:
            raise RuntimeError("fail first scheduler attempt")
        return original_generate_items(**kwargs)

    monkeypatch.setattr(real_service, "_generate_items", fail_once)
    plan_calls = []

    class RecordingPlanService:
        def generate_plan(self, **kwargs):
            plan_calls.append(kwargs.copy())
            return real_service.generate_plan(**kwargs)

    service = DataJobService(
        repo,
        FakeMarketDataService(),
        settings,
        plan_service=RecordingPlanService(),
    )
    job_id = repo.create_job(
        "generate_daily_plan",
        "generate-retry-force",
        {"trade_date": TRADE_DATE.isoformat()},
    )

    first = service.run_next_queued_job(owner_id="worker-a", now=NOW)
    second = service.run_next_queued_job(
        owner_id="worker-a", now=NOW + timedelta(minutes=1)
    )

    assert first["status"] == "QUEUED"
    assert first["error"]["code"] == "PLAN_GENERATION_FAILED"
    assert second["status"] == "SUCCEEDED"
    assert plan_calls[0].get("force_new_version", False) is False
    assert plan_calls[1]["force_new_version"] is True
    runs = sorted(repo.list_plan_runs(), key=lambda run: run["id"])
    assert len(runs) == 2
    assert runs[0]["status"] == "SUPERSEDED"
    assert runs[0]["error"]["code"] == "PLAN_GENERATION_FAILED"
    assert runs[1]["status"] in ("READY", "PARTIAL")
    assert repo.get_job(job_id)["result"]["plans"][0]["id"] == runs[1]["id"]


def test_sync_pool_from_hotlist_uses_pool_service_and_is_idempotent(
    repo, settings
):
    hotlist_repo = HotlistRepository(repo)
    pool_service = PoolService(hotlist_repo)
    service = DataJobService(
        hotlist_repo,
        FakeMarketDataService(),
        settings,
        pool_service=pool_service,
    )

    _, first = _run(
        service, hotlist_repo, "sync_pool_from_hotlist", "hotlist-1"
    )
    _, second = _run(
        service, hotlist_repo, "sync_pool_from_hotlist", "hotlist-2"
    )

    assert first["status"] == second["status"] == "SUCCEEDED"
    assert first["result"]["pool"]["reused"] is False
    assert second["result"]["pool"]["reused"] is True
    assert set(repo.get_latest_pool_codes("default")) == {
        "000001.SZ",
        "600000.SH",
    }


def _create_reconcile_fixture(repo):
    account = repo.create_account(
        name="main", initial_equity=100_000, cash_balance=100_000
    )
    repo.upsert_position(
        account_id=account["id"],
        stock_code="000001.SZ",
        quantity=100,
        available_quantity=0,
        average_cost=10,
    )
    strategy = _create_active_strategy(repo)
    pool = _create_pool(repo)
    run = repo.create_plan_run(
        run_key="reconcile-run",
        account_id=account["id"],
        signal_date="2026-08-18",
        target_trade_date=TRADE_DATE.isoformat(),
        stock_pool_version_id=pool["id"],
        strategy_version_id=strategy["id"],
        status="READY",
        account_snapshot_json={},
        data_snapshot_hash="snapshot",
    )
    repo.create_plan_item(
        plan_run_id=run["id"],
        stock_code="000001.SZ",
        action="CONDITIONAL_BUY",
        do_not_chase_price=10,
    )
    repo.upsert_daily_bars(
        [
            DailyBar(
                code="000001.SZ",
                trade_date=TRADE_DATE,
                open=10.5,
                high=11,
                low=10.2,
                close=10.8,
                volume=1_000,
                source="fake",
            )
        ]
    )
    return account, run


def test_reconcile_orders_persists_not_filled_via_cas_and_replays_idempotently(
    repo, settings
):
    account, run = _create_reconcile_fixture(repo)
    service = DataJobService(repo, FakeMarketDataService(), settings)

    _, first = _run(service, repo, "reconcile_orders", "reconcile-1")
    _, second = _run(service, repo, "reconcile_orders", "reconcile-2")

    assert first["status"] == second["status"] == "SUCCEEDED"
    assert first["result"] == {
        "outcome": "COMPLETED",
        "accounts_rolled": 1,
        "items_not_filled": 1,
    }
    assert second["result"]["items_not_filled"] == 0
    assert repo.get_position(account["id"], "000001.SZ")[
        "available_quantity"
    ] == 100
    assert repo.get_plan_items(run["id"])[0][
        "execution_status"
    ] == "NOT_FILLED"


def test_reconcile_orders_does_not_unlock_same_day_buy(repo, settings):
    account = repo.create_account(
        name="main", initial_equity=100_000, cash_balance=100_000
    )
    ExecutionService(repo).record_execution(
        account_id=account["id"],
        stock_code="000001.SZ",
        side="BUY",
        trade_date=TRADE_DATE.isoformat(),
        price=10,
        quantity=100,
        client_execution_id="same-day-buy",
    )
    service = DataJobService(repo, FakeMarketDataService(), settings)

    _, result = _run(service, repo, "reconcile_orders", "same-day-t1")

    assert result["status"] == "SUCCEEDED"
    assert result["result"]["accounts_rolled"] == 0
    assert repo.get_position(account["id"], "000001.SZ")[
        "available_quantity"
    ] == 0


def test_reconcile_orders_stops_before_later_account_after_lease_fence_fails(
    repo,
):
    accounts = [
        repo.create_account(
            name=name, initial_equity=100_000, cash_balance=100_000
        )
        for name in ("first", "second")
    ]
    for account in accounts:
        repo.upsert_position(
            account_id=account["id"],
            stock_code="000001.SZ",
            quantity=100,
            available_quantity=0,
            average_cost=10,
        )
    fence_calls = 0

    def lose_before_second_account():
        nonlocal fence_calls
        fence_calls += 1
        if fence_calls == 2:
            raise LeaseLostError("lease lost before second account")

    with pytest.raises(LeaseLostError, match="lease lost"):
        ExecutionService(repo).reconcile_orders(
            TRADE_DATE, check_fence=lose_before_second_account
        )

    assert repo.get_position(accounts[0]["id"], "000001.SZ")[
        "available_quantity"
    ] == 100
    assert repo.get_position(accounts[1]["id"], "000001.SZ")[
        "available_quantity"
    ] == 0


def test_backup_database_requires_verified_backup_result(repo, settings):
    calls = []

    def verified_backup(db_path):
        calls.append(db_path)
        return {
            "path": "backup.db",
            "size": 123,
            "integrity_ok": True,
            "integrity_result": ["ok"],
        }

    service = DataJobService(
        repo,
        FakeMarketDataService(),
        settings,
        backup_fn=verified_backup,
    )

    _, result = _run(
        service, repo, "backup_database", "verified-backup"
    )

    assert result["status"] == "SUCCEEDED"
    assert result["result"] == {
        "outcome": "COMPLETED",
        "backup": {
            "path": "backup.db",
            "size": 123,
            "integrity_ok": True,
            "integrity_result": ["ok"],
        },
    }
    assert calls == [settings.db_path]


@pytest.mark.parametrize(
    "backup_result",
    [
        {"path": "", "integrity_ok": True},
        {
            "path": "backup.db",
            "integrity_ok": False,
            "integrity_result": ["corrupt"],
        },
    ],
    ids=["missing-path", "integrity-failed"],
)
def test_invalid_backup_retries_three_times_and_persists_structured_error(
    repo, settings, backup_result
):
    service = DataJobService(
        repo,
        FakeMarketDataService(),
        settings,
        backup_fn=lambda _: backup_result,
    )
    job_id = repo.create_job(
        "backup_database",
        f"backup-failure-{backup_result.get('path')}",
        {"trade_date": TRADE_DATE.isoformat()},
    )

    statuses = [
        service.run_next_queued_job(
            owner_id="worker-a", now=NOW + timedelta(minutes=attempt)
        )["status"]
        for attempt in range(3)
    ]

    assert statuses == ["QUEUED", "QUEUED", "FAILED"]
    final = repo.get_job(job_id)
    assert final["attempts"] == 3
    assert final["result"] is None
    assert final["error"] == {
        "code": "JOB_EXECUTION_FAILED",
        "message": "数据库备份或完整性检查失败",
        "details": {"type": "RuntimeError"},
    }
    with sqlite3.connect(repo.db_path) as conn:
        raw = conn.execute(
            "SELECT attempts, result_json, error_json "
            "FROM trade_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    assert raw[0] == 3
    assert raw[1] is None
    assert json.loads(raw[2]) == final["error"]
