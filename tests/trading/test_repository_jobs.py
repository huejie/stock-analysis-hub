from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Thread

import pytest

from backend.trading.domain import TradeDay
from backend.trading.migrations import run_migrations
from backend.trading.repository import TradingRepository


@pytest.fixture
def repos(tmp_path):
    db_path = str(tmp_path / "jobs.db")
    run_migrations(db_path)
    return TradingRepository(db_path), TradingRepository(db_path)


def test_two_repositories_only_claim_job_once(repos):
    repo_a, repo_b = repos
    job_id = repo_a.create_job("update_bars", "claim-once", {"stock_codes": []})
    now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    barrier = Barrier(2)

    def claim(repo, owner_id):
        barrier.wait()
        return repo.claim_next_job(owner_id=owner_id, now=now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, (repo_a, repo_b), ("a", "b")))

    winners = [claimed for claimed in results if claimed is not None]
    assert len(winners) == 1
    assert winners[0]["id"] == job_id
    assert winners[0]["status"] == "RUNNING"
    assert winners[0]["attempts"] == 1
    assert winners[0]["claimed_by"] in {"a", "b"}


def test_retry_twice_then_fail_on_third_attempt(repos):
    repo, _ = repos
    job_id = repo.create_job("update_bars", "retry-three", {})
    now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    expected = ["QUEUED", "QUEUED", "FAILED"]
    for index, expected_status in enumerate(expected):
        claimed = repo.claim_next_job(
            owner_id="worker", now=now + timedelta(minutes=index)
        )
        attempt_now = now + timedelta(minutes=index)
        assert repo.acquire_job_execution_lease(
            job_id,
            owner_id="worker",
            expected_attempt=claimed["attempts"],
            now=attempt_now,
            expires_at=attempt_now + timedelta(minutes=1),
        )
        assert claimed["id"] == job_id
        status = repo.retry_or_fail_job(
            job_id,
            error={"code": "PROVIDER_UNAVAILABLE", "message": "offline"},
            owner_id="worker",
            expected_attempt=claimed["attempts"],
            now=attempt_now,
            max_attempts=3,
        )
        assert status == expected_status

    final = repo.get_job(job_id)
    assert final["attempts"] == 3
    assert final["status"] == "FAILED"
    assert final["error"]["code"] == "PROVIDER_UNAVAILABLE"


def test_requeue_stale_running_job(repos):
    repo, _ = repos
    job_id = repo.create_job("validate_data", "stale", {})
    started = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    repo.claim_next_job(owner_id="dead-worker", now=started)

    changed = repo.requeue_stale_jobs(
        stale_before=started + timedelta(minutes=30),
        now=started + timedelta(minutes=31),
    )

    assert changed == 1
    assert repo.get_job(job_id)["status"] == "QUEUED"


def test_plan_wait_job_requeues_immediately_after_generation_lease_released(repos):
    """计划等待任务在 generation lease 释放后无需等待普通陈旧阈值。"""
    repo, _ = repos
    started = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    job_id = repo.create_job(
        "generate_daily_plan",
        "wait-for-live-plan",
        {"trade_date": "2026-07-31"},
    )
    claimed = repo.claim_next_job(owner_id="scheduler", now=started)
    assert repo.acquire_job_execution_lease(
        job_id,
        owner_id="scheduler",
        expected_attempt=claimed["attempts"],
        now=started,
        expires_at=started + timedelta(minutes=10),
    )
    run = repo.create_plan_run(
        run_key="generating-plan-run", account_id=1,
        signal_date="2026-07-31", target_trade_date="2026-08-01",
        stock_pool_version_id=1, strategy_version_id=1, status="GENERATING",
        account_snapshot_json={}, data_snapshot_hash="snapshot",
    )
    assert repo.acquire_plan_generation_lease(
        run["id"], owner_id="web-owner", now=started,
        expires_at=started + timedelta(minutes=60),
    )
    assert repo.wait_for_plan_generation(
        job_id,
        error={"code": "PLAN_IN_PROGRESS", "message": "plan is running"},
        owner_id="scheduler",
        expected_attempt=claimed["attempts"],
        now=started,
    )
    assert repo.release_job_execution_lease(
        job_id,
        owner_id="scheduler",
        expected_attempt=claimed["attempts"],
    )

    assert repo.requeue_stale_jobs(
        stale_before=started - timedelta(minutes=29),
        now=started + timedelta(minutes=1),
    ) == 0
    assert repo.get_job(job_id)["status"] == "RUNNING"

    assert repo.release_plan_generation_lease(
        run["id"], owner_id="web-owner"
    )
    assert repo.requeue_stale_jobs(
        stale_before=started - timedelta(minutes=28),
        now=started + timedelta(minutes=2),
    ) == 1
    assert repo.get_job(job_id)["status"] == "QUEUED"


def test_expired_generation_lease_fences_plan_transition_and_finalization(repos):
    """写事务内的 lease 谓词失效时，状态和 plan items 都不可落库。"""
    repo, _ = repos
    started = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    expired_write = started + timedelta(seconds=2)
    created = repo.create_plan_run(
        run_key="expired-generation-write", account_id=1,
        signal_date="2026-07-31", target_trade_date="2026-08-01",
        stock_pool_version_id=1, strategy_version_id=1, status="CREATED",
        account_snapshot_json={}, data_snapshot_hash="snapshot",
    )
    assert repo.acquire_plan_generation_lease(
        created["id"], owner_id="expired-owner", now=started,
        expires_at=started + timedelta(seconds=1),
    )

    assert repo.transition_plan_run_status(
        created["id"], "VALIDATING", expected_statuses=("CREATED",),
        generation_owner_id="expired-owner", now=expired_write,
    ) is False
    assert repo.get_plan_run(created["id"])["status"] == "CREATED"

    repo.update_plan_run_status(created["id"], "GENERATING")
    final = repo.finalize_plan_run(
        created["id"], status="READY",
        items=({"stock_code": "000001.SZ", "action": "CONDITIONAL_BUY"},),
        market_regime="ATTACK", market_score=80, recommended_exposure=0.5,
        warnings=[], degraded=False,
        generation_owner_id="expired-owner", now=expired_write,
    )

    assert final["status"] == "GENERATING"
    assert repo.get_plan_items(created["id"]) == []


def test_stale_claim_generation_cannot_complete_or_retry_new_claim(repos):
    repo_a, repo_b = repos
    job_id = repo_a.create_job("validate_data", "stale-generation", {})
    started = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    claimed_a = repo_a.claim_next_job(owner_id="a", now=started)
    repo_a.requeue_stale_jobs(
        stale_before=started + timedelta(minutes=30),
        now=started + timedelta(minutes=31),
    )
    claimed_b = repo_b.claim_next_job(
        owner_id="b", now=started + timedelta(minutes=31)
    )
    assert repo_b.acquire_job_execution_lease(
        job_id,
        owner_id="b",
        expected_attempt=claimed_b["attempts"],
        now=started + timedelta(minutes=31),
        expires_at=started + timedelta(minutes=41),
    )

    with pytest.raises(ValueError):
        repo_a.complete_job(
            job_id,
            result={"worker": "a"},
            owner_id="a",
            expected_attempt=claimed_a["attempts"],
            now=started + timedelta(minutes=31),
        )
    with pytest.raises(ValueError):
        repo_a.retry_or_fail_job(
            job_id,
            error={"code": "LATE_WORKER", "message": "stale claim"},
            owner_id="a",
            expected_attempt=claimed_a["attempts"],
            now=started + timedelta(minutes=31),
        )

    repo_b.complete_job(
        job_id,
        result={"worker": "b"},
        owner_id="b",
        expected_attempt=claimed_b["attempts"],
        now=started + timedelta(minutes=31),
    )
    final = repo_b.get_job(job_id)
    assert final["status"] == "SUCCEEDED"
    assert final["result"] == {"worker": "b"}


def test_job_lock_owner_and_expiry(repos):
    repo_a, repo_b = repos
    now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    assert repo_a.acquire_job_lock(
        lock_key="schedule:update:2026-07-31",
        owner_id="a",
        now=now,
        expires_at=now + timedelta(minutes=10),
    )
    assert not repo_b.acquire_job_lock(
        lock_key="schedule:update:2026-07-31",
        owner_id="b",
        now=now + timedelta(minutes=1),
        expires_at=now + timedelta(minutes=11),
    )
    repo_b.release_job_lock(
        lock_key="schedule:update:2026-07-31", owner_id="b"
    )
    assert not repo_b.acquire_job_lock(
        lock_key="schedule:update:2026-07-31",
        owner_id="b",
        now=now + timedelta(minutes=2),
        expires_at=now + timedelta(minutes=12),
    )
    assert repo_b.acquire_job_lock(
        lock_key="schedule:update:2026-07-31",
        owner_id="b",
        now=now + timedelta(minutes=11),
        expires_at=now + timedelta(minutes=21),
    )


def test_execution_lease_acquire_atomically_validates_generation(repos):
    repo_a, repo_b = repos
    job_id = repo_a.create_job("update_bars", "lease-generation", {})
    started = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    claimed_a = repo_a.claim_next_job(owner_id="a", now=started)
    repo_a.requeue_stale_jobs(
        stale_before=started + timedelta(minutes=30),
        now=started + timedelta(minutes=31),
    )
    claimed_b = repo_b.claim_next_job(
        owner_id="b", now=started + timedelta(minutes=31)
    )

    stale_lease = repo_a.acquire_job_execution_lease(
        job_id,
        owner_id="a",
        expected_attempt=claimed_a["attempts"],
        now=started + timedelta(minutes=31),
        expires_at=started + timedelta(minutes=41),
    )
    current_lease = repo_b.acquire_job_execution_lease(
        job_id,
        owner_id="b",
        expected_attempt=claimed_b["attempts"],
        now=started + timedelta(minutes=31),
        expires_at=started + timedelta(minutes=41),
    )

    assert stale_lease is None
    assert current_lease["status"] == "RUNNING"
    assert current_lease["attempts"] == claimed_b["attempts"] == 2


def test_active_execution_lease_blocks_stale_requeue(repos):
    repo, _ = repos
    job_id = repo.create_job("update_bars", "active-lease", {})
    started = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    claimed = repo.claim_next_job(owner_id="worker", now=started)
    lease = repo.acquire_job_execution_lease(
        job_id,
        owner_id="worker",
        expected_attempt=claimed["attempts"],
        now=started,
        expires_at=started + timedelta(minutes=10),
    )

    changed = repo.requeue_stale_jobs(
        stale_before=started + timedelta(minutes=30),
        now=started + timedelta(minutes=5),
    )

    assert lease["attempts"] == claimed["attempts"]
    assert changed == 0
    assert repo.get_job(job_id)["status"] == "RUNNING"


def test_execution_lease_cannot_be_acquired_twice_by_same_owner(repos):
    repo, _ = repos
    job_id = repo.create_job("update_bars", "lease-not-reentrant", {})
    now = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    claimed = repo.claim_next_job(owner_id="worker", now=now)

    first = repo.acquire_job_execution_lease(
        job_id,
        owner_id="worker",
        expected_attempt=claimed["attempts"],
        now=now,
        expires_at=now + timedelta(minutes=10),
    )
    second = repo.acquire_job_execution_lease(
        job_id,
        owner_id="worker",
        expected_attempt=claimed["attempts"],
        now=now + timedelta(minutes=1),
        expires_at=now + timedelta(minutes=11),
    )

    assert first["attempts"] == claimed["attempts"]
    assert second is None


def test_different_owner_cannot_acquire_expired_lease_in_same_attempt(repos):
    repo_a, repo_b = repos
    job_id = repo_a.create_job("update_bars", "expired-owner-acquire", {})
    started = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    claimed = repo_a.claim_next_job(owner_id="a", now=started)
    assert repo_a.acquire_job_execution_lease(
        job_id,
        owner_id="a",
        expected_attempt=claimed["attempts"],
        now=started,
        expires_at=started + timedelta(minutes=1),
    )

    stolen = repo_b.acquire_job_execution_lease(
        job_id,
        owner_id="b",
        expected_attempt=claimed["attempts"],
        now=started + timedelta(minutes=2),
        expires_at=started + timedelta(minutes=12),
    )

    assert stolen is None
    assert repo_b.get_job(job_id)["attempts"] == 1


def test_different_owner_cannot_reacquire_when_reaper_sees_expired_lease(repos):
    repo_a, repo_b = repos
    job_id = repo_a.create_job("update_bars", "expired-owner-reacquire", {})
    started = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    claimed = repo_a.claim_next_job(owner_id="a", now=started)
    assert repo_a.acquire_job_execution_lease(
        job_id,
        owner_id="a",
        expected_attempt=claimed["attempts"],
        now=started,
        expires_at=started + timedelta(minutes=1),
    )
    assert repo_b.requeue_stale_jobs(
        stale_before=started - timedelta(minutes=1),
        now=started + timedelta(minutes=2),
    ) == 0

    assert not repo_b.renew_or_reacquire_job_execution_lease(
        job_id,
        owner_id="b",
        expected_attempt=claimed["attempts"],
        now=started + timedelta(minutes=2),
        expires_at=started + timedelta(minutes=12),
    )


def test_expired_execution_lease_allows_stale_recovery(repos):
    repo, _ = repos
    job_id = repo.create_job("update_bars", "expired-lease", {})
    started = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    claimed = repo.claim_next_job(owner_id="worker", now=started)
    repo.acquire_job_execution_lease(
        job_id,
        owner_id="worker",
        expected_attempt=claimed["attempts"],
        now=started,
        expires_at=started + timedelta(minutes=10),
    )

    changed = repo.requeue_stale_jobs(
        stale_before=started + timedelta(minutes=30),
        now=started + timedelta(minutes=11),
    )

    assert changed == 1
    assert repo.get_job(job_id)["status"] == "QUEUED"


def test_old_attempt_cannot_renew_or_release_new_execution_lease(repos):
    repo_a, repo_b = repos
    job_id = repo_a.create_job("update_bars", "lease-fencing", {})
    started = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    claimed_a = repo_a.claim_next_job(owner_id="a", now=started)
    repo_a.acquire_job_execution_lease(
        job_id,
        owner_id="a",
        expected_attempt=claimed_a["attempts"],
        now=started,
        expires_at=started + timedelta(minutes=10),
    )
    repo_a.requeue_stale_jobs(
        stale_before=started + timedelta(minutes=30),
        now=started + timedelta(minutes=11),
    )
    claimed_b = repo_b.claim_next_job(
        owner_id="b", now=started + timedelta(minutes=11)
    )
    repo_b.acquire_job_execution_lease(
        job_id,
        owner_id="b",
        expected_attempt=claimed_b["attempts"],
        now=started + timedelta(minutes=11),
        expires_at=started + timedelta(minutes=21),
    )

    assert not repo_a.renew_job_execution_lease(
        job_id,
        owner_id="a",
        expected_attempt=claimed_a["attempts"],
        now=started + timedelta(minutes=12),
        expires_at=started + timedelta(minutes=22),
    )
    assert not repo_a.release_job_execution_lease(
        job_id,
        owner_id="a",
        expected_attempt=claimed_a["attempts"],
    )
    assert repo_b.renew_job_execution_lease(
        job_id,
        owner_id="b",
        expected_attempt=claimed_b["attempts"],
        now=started + timedelta(minutes=12),
        expires_at=started + timedelta(minutes=22),
    )
    with pytest.raises(ValueError):
        repo_a.complete_job(
            job_id,
            result={"worker": "a"},
            owner_id="a",
            expected_attempt=claimed_a["attempts"],
            now=started + timedelta(minutes=12),
        )

    assert repo_b.release_job_execution_lease(
        job_id,
        owner_id="b",
        expected_attempt=claimed_b["attempts"],
    )


def test_heartbeat_renewal_refreshes_job_liveness_timestamp(repos):
    repo, _ = repos
    job_id = repo.create_job("update_bars", "heartbeat-liveness", {})
    started = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    claimed = repo.claim_next_job(owner_id="worker", now=started)
    repo.acquire_job_execution_lease(
        job_id,
        owner_id="worker",
        expected_attempt=claimed["attempts"],
        now=started,
        expires_at=started + timedelta(minutes=10),
    )
    heartbeat_at = started + timedelta(minutes=5)

    assert repo.renew_job_execution_lease(
        job_id,
        owner_id="worker",
        expected_attempt=claimed["attempts"],
        now=heartbeat_at,
        expires_at=heartbeat_at + timedelta(minutes=10),
    )

    assert repo.get_job(job_id)["started_at"] == "2026-07-31 10:05:00"


@pytest.mark.parametrize("terminal", ["complete", "retry"])
def test_terminal_write_rejects_missing_or_expired_execution_lease(
    repos, terminal
):
    repo, _ = repos
    job_id = repo.create_job("update_bars", f"fenced-{terminal}", {})
    now = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    claimed = repo.claim_next_job(owner_id="worker", now=now)
    repo.acquire_job_execution_lease(
        job_id,
        owner_id="worker",
        expected_attempt=claimed["attempts"],
        now=now,
        expires_at=now + timedelta(minutes=1),
    )

    with pytest.raises(ValueError, match="有效 execution lease"):
        if terminal == "complete":
            repo.complete_job(
                job_id,
                result={"rows": 1},
                owner_id="worker",
                expected_attempt=claimed["attempts"],
                now=now + timedelta(minutes=2),
            )
        else:
            repo.retry_or_fail_job(
                job_id,
                error={"code": "FAILED", "message": "boom"},
                owner_id="worker",
                expected_attempt=claimed["attempts"],
                now=now + timedelta(minutes=2),
            )

    assert repo.get_job(job_id)["status"] == "RUNNING"


def test_renew_or_reacquire_recovers_expired_lease_for_same_worker(repos):
    repo, _ = repos
    job_id = repo.create_job("update_bars", "lease-reacquire", {})
    now = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    claimed = repo.claim_next_job(owner_id="worker", now=now)
    repo.acquire_job_execution_lease(
        job_id,
        owner_id="worker",
        expected_attempt=claimed["attempts"],
        now=now,
        expires_at=now + timedelta(seconds=1),
    )

    assert repo.renew_or_reacquire_job_execution_lease(
        job_id,
        owner_id="worker",
        expected_attempt=claimed["attempts"],
        now=now + timedelta(seconds=1),
        expires_at=now + timedelta(minutes=1),
    )

    assert repo.get_job(job_id)["started_at"] == "2026-07-31 10:00:01"


def test_reaper_and_heartbeat_race_leave_only_one_valid_attempt(repos):
    repo_a, repo_b = repos
    job_id = repo_a.create_job("update_bars", "reaper-heartbeat-race", {})
    started = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    claimed_a = repo_a.claim_next_job(owner_id="a", now=started)
    repo_a.acquire_job_execution_lease(
        job_id,
        owner_id="a",
        expected_attempt=claimed_a["attempts"],
        now=started,
        expires_at=started + timedelta(seconds=1),
    )
    race_at = started + timedelta(seconds=1)
    barrier = Barrier(2)
    outcome = {}

    def heartbeat():
        barrier.wait()
        outcome["renewed"] = (
            repo_a.renew_or_reacquire_job_execution_lease(
                job_id,
                owner_id="a",
                expected_attempt=claimed_a["attempts"],
                now=race_at,
                expires_at=race_at + timedelta(minutes=1),
            )
        )

    def reap():
        barrier.wait()
        outcome["requeued"] = repo_b.requeue_stale_jobs(
            stale_before=race_at + timedelta(minutes=1),
            now=race_at,
        )

    threads = [Thread(target=heartbeat), Thread(target=reap)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    current = repo_a.get_job(job_id)
    if outcome["renewed"]:
        assert outcome["requeued"] == 0
        assert current["status"] == "RUNNING"
        assert current["attempts"] == 1
    else:
        assert outcome["requeued"] == 1
        claimed_b = repo_b.claim_next_job(owner_id="b", now=race_at)
        assert claimed_b["attempts"] == 2
        assert repo_b.acquire_job_execution_lease(
            job_id,
            owner_id="b",
            expected_attempt=claimed_b["attempts"],
            now=race_at,
            expires_at=race_at + timedelta(minutes=1),
        )


def test_trade_calendar_round_trip(repos):
    repo, _ = repos
    days = [
        TradeDay(date(2026, 10, 1), False),
        TradeDay(date(2026, 10, 9), True),
    ]

    assert repo.upsert_trade_calendar(days, source="akshare") == 2
    assert repo.get_trade_day(date(2026, 10, 1)).is_open is False
    assert repo.get_trade_day(date(2026, 10, 9)).is_open is True
