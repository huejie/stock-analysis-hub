import os
from threading import Barrier, Thread

import pytest
from backend.trading.repository import TradingRepository
from backend.trading.migrations import run_migrations

TEST_DB = "data/test_repo_phase3.db"


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


# ---- Strategy methods ----

def test_create_strategy_version_draft(repo):
    out = repo.create_strategy_version(
        strategy_code="default", name="v1",
        params_json={"risk_per_trade": 0.005, "min_score": 70},
    )
    assert out["id"] > 0
    assert out["version_no"] == 1
    assert out["status"] == "DRAFT"
    assert out["reused"] is False
    assert len(out["params_hash"]) == 16


def test_create_strategy_version_idempotent_same_params(repo):
    """相同 params_json(键顺序不同也算同一)→ 返回已有,reused=True。"""
    p1 = {"risk_per_trade": 0.005, "min_score": 70}
    p2 = {"min_score": 70, "risk_per_trade": 0.005}  # 键顺序不同
    v1 = repo.create_strategy_version(strategy_code="default", name="v1", params_json=p1)
    v2 = repo.create_strategy_version(strategy_code="default", name="v1-dup", params_json=p2)
    assert v1["id"] == v2["id"]
    assert v1["params_hash"] == v2["params_hash"]
    assert v2["reused"] is True


def test_create_strategy_version_increment_per_code(repo):
    """同一 strategy_code 不同 params → version_no 递增。"""
    v1 = repo.create_strategy_version(strategy_code="default", name="v1", params_json={"a": 1})
    v2 = repo.create_strategy_version(strategy_code="default", name="v2", params_json={"a": 2})
    assert v1["version_no"] == 1
    assert v2["version_no"] == 2
    assert v1["id"] != v2["id"]
    assert v1["params_hash"] != v2["params_hash"]


def test_create_strategy_version_independent_per_code(repo):
    """不同 strategy_code 的 version_no 独立(各从 1 开始)。"""
    a1 = repo.create_strategy_version(strategy_code="default", name="d1", params_json={"a": 1})
    b1 = repo.create_strategy_version(strategy_code="aggressive", name="a1", params_json={"a": 1})
    assert a1["version_no"] == 1
    assert b1["version_no"] == 1
    # params_hash 全局 UNIQUE(跨 code 也唯一):相同 params_json 不同 code → 同 hash → 复用
    # 注意:实际生产 params 不会完全相同,这里验证 hash 行为
    assert a1["params_hash"] == b1["params_hash"]
    assert b1["reused"] is True  # 跨 code 命中同一 hash


def test_get_strategy(repo):
    created = repo.create_strategy_version(strategy_code="default", name="v1", params_json={"x": 1})
    got = repo.get_strategy(created["id"])
    assert got is not None
    assert got["strategy_code"] == "default"
    assert got["params_json"] == {"x": 1}
    assert got["status"] == "DRAFT"


def test_get_strategy_not_found(repo):
    assert repo.get_strategy(99999) is None


def test_list_strategies_filter_by_code(repo):
    repo.create_strategy_version(strategy_code="default", name="v1", params_json={"a": 1})
    repo.create_strategy_version(strategy_code="default", name="v2", params_json={"a": 2})
    repo.create_strategy_version(strategy_code="aggressive", name="a1", params_json={"z": 9})
    default_list = repo.list_strategies(strategy_code="default")
    assert len(default_list) == 2
    all_list = repo.list_strategies()
    assert len(all_list) == 3


def test_get_active_strategy_none_when_no_active(repo):
    repo.create_strategy_version(strategy_code="default", name="v1", params_json={"a": 1})
    assert repo.get_active_strategy("default") is None


def test_activate_strategy_returns_previous_active_id(repo):
    v1 = repo.create_strategy_version(strategy_code="default", name="v1", params_json={"a": 1})
    v2 = repo.create_strategy_version(strategy_code="default", name="v2", params_json={"a": 2})
    # 第一次激活:之前无 ACTIVE
    prev1 = repo.activate_strategy(v1["id"])
    assert prev1 is None
    assert repo.get_strategy(v1["id"])["status"] == "ACTIVE"
    # 第二次激活:旧 ACTIVE 是 v1,返回 v1 id
    prev2 = repo.activate_strategy(v2["id"])
    assert prev2 == v1["id"]
    # v1 变 RETIRED,v2 变 ACTIVE
    assert repo.get_strategy(v1["id"])["status"] == "RETIRED"
    assert repo.get_strategy(v2["id"])["status"] == "ACTIVE"
    # activated_at 已设置
    assert repo.get_strategy(v2["id"])["activated_at"] is not None
    # get_active_strategy 返回 v2
    active = repo.get_active_strategy("default")
    assert active["id"] == v2["id"]


def test_activate_strategy_other_code_unaffected(repo):
    """激活某 code 不影响其它 code 的状态。"""
    a1 = repo.create_strategy_version(strategy_code="default", name="d1", params_json={"a": 1})
    b1 = repo.create_strategy_version(strategy_code="aggressive", name="a1", params_json={"b": 2})
    repo.activate_strategy(a1["id"])
    repo.activate_strategy(b1["id"])
    # 两个 code 都应各自有一个 ACTIVE
    assert repo.get_active_strategy("default")["id"] == a1["id"]
    assert repo.get_active_strategy("aggressive")["id"] == b1["id"]


# ---- Plan run methods ----

def _make_account(repo):
    return repo.create_account(name="main", initial_equity=100000, cash_balance=100000)


def test_create_plan_run(repo):
    acc = _make_account(repo)
    out = repo.create_plan_run(
        run_key="rk-001", account_id=acc["id"], signal_date="2026-07-22",
        target_trade_date="2026-07-23", stock_pool_version_id=1,
        strategy_version_id=1, status="CREATED",
        account_snapshot_json={"cash": 100000}, data_snapshot_hash="snap-1",
    )
    assert out["id"] > 0
    assert out["reused"] is False


def test_create_plan_run_idempotent_on_run_key(repo):
    acc = _make_account(repo)
    kwargs = dict(
        run_key="rk-001", account_id=acc["id"], signal_date="2026-07-22",
        target_trade_date="2026-07-23", stock_pool_version_id=1,
        strategy_version_id=1, status="CREATED",
        account_snapshot_json={"cash": 100000}, data_snapshot_hash="snap-1",
    )
    r1 = repo.create_plan_run(**kwargs)
    r2 = repo.create_plan_run(**kwargs)
    assert r1["id"] == r2["id"]
    assert r2["reused"] is True


class _PlanCreateRaceConnection:
    """Force the legacy SELECT/INSERT gap without delaying atomic code."""

    def __init__(self, inner, select_barrier):
        self._inner = inner
        self._select_barrier = select_barrier
        self._atomic = False

    def execute(self, sql, params=()):
        if sql.strip().upper() == "BEGIN IMMEDIATE":
            self._atomic = True
        cursor = self._inner.execute(sql, params)
        if (
            not self._atomic
            and "SELECT id FROM trade_plan_runs WHERE run_key" in sql
        ):
            self._select_barrier.wait(timeout=3)
        return cursor

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_create_plan_run_concurrent_callers_have_one_owner(repo, monkeypatch):
    account = _make_account(repo)
    barrier = Barrier(2)
    repo_a = TradingRepository(TEST_DB)
    repo_b = TradingRepository(TEST_DB)

    for instance in (repo_a, repo_b):
        original_conn = instance._conn
        monkeypatch.setattr(
            instance,
            "_conn",
            lambda original_conn=original_conn: _PlanCreateRaceConnection(
                original_conn(), barrier
            ),
        )

    kwargs = dict(
        run_key="rk-concurrent",
        account_id=account["id"],
        signal_date="2026-07-22",
        target_trade_date="2026-07-23",
        stock_pool_version_id=1,
        strategy_version_id=1,
        status="CREATED",
        account_snapshot_json={"cash": 100000},
        data_snapshot_hash="snap-concurrent",
    )
    results = []
    errors = []

    def create(instance):
        try:
            results.append(instance.create_plan_run(**kwargs))
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=create, args=(instance,)) for instance in (repo_a, repo_b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert {result["id"] for result in results} == {results[0]["id"]}
    assert sorted(result["reused"] for result in results) == [False, True]


def test_get_plan_run(repo):
    acc = _make_account(repo)
    created = repo.create_plan_run(
        run_key="rk-001", account_id=acc["id"], signal_date="2026-07-22",
        target_trade_date="2026-07-23", stock_pool_version_id=1,
        strategy_version_id=1, status="CREATED",
        account_snapshot_json={"cash": 100000}, data_snapshot_hash="snap-1",
    )
    got = repo.get_plan_run(created["id"])
    assert got is not None
    assert got["run_key"] == "rk-001"
    assert got["status"] == "CREATED"


def test_get_plan_run_by_key(repo):
    acc = _make_account(repo)
    repo.create_plan_run(
        run_key="rk-001", account_id=acc["id"], signal_date="2026-07-22",
        target_trade_date="2026-07-23", stock_pool_version_id=1,
        strategy_version_id=1, status="CREATED",
        account_snapshot_json={"cash": 100000}, data_snapshot_hash="snap-1",
    )
    got = repo.get_plan_run_by_key("rk-001")
    assert got is not None
    assert got["signal_date"] == "2026-07-22"
    assert repo.get_plan_run_by_key("nope") is None


def test_list_plan_runs_filters(repo):
    acc = _make_account(repo)
    other = repo.create_account(
        name="secondary", initial_equity=50000, cash_balance=50000,
        is_active=False,
    )
    first = repo.create_plan_run(
        run_key="rk-1", account_id=acc["id"], signal_date="2026-07-22",
        target_trade_date="2026-07-23", stock_pool_version_id=1,
        strategy_version_id=1, status="READY",
        account_snapshot_json={"cash": 100000}, data_snapshot_hash="s1",
    )
    second = repo.create_plan_run(
        run_key="rk-2", account_id=other["id"], signal_date="2026-07-23",
        target_trade_date="2026-07-24", stock_pool_version_id=1,
        strategy_version_id=1, status="BLOCKED",
        account_snapshot_json={"cash": 50000}, data_snapshot_hash="s2",
    )
    assert len(repo.list_plan_runs(signal_date="2026-07-22")) == 1
    assert len(repo.list_plan_runs(status="BLOCKED")) == 1
    assert len(repo.list_plan_runs()) == 2
    assert [row["id"] for row in repo.list_plan_runs(account_id=acc["id"])] == [
        first["id"]
    ]
    assert [
        row["id"] for row in repo.list_plan_runs(account_id=other["id"])
    ] == [second["id"]]


def test_update_plan_run_status(repo):
    acc = _make_account(repo)
    created = repo.create_plan_run(
        run_key="rk-1", account_id=acc["id"], signal_date="2026-07-22",
        target_trade_date="2026-07-23", stock_pool_version_id=1,
        strategy_version_id=1, status="CREATED",
        account_snapshot_json={"cash": 100000}, data_snapshot_hash="s1",
    )
    repo.update_plan_run_status(created["id"], "READY",
                                market_regime="ATTACK", market_score=2,
                                recommended_exposure=0.60)
    got = repo.get_plan_run(created["id"])
    assert got["status"] == "READY"
    assert got["market_regime"] == "ATTACK"
    assert got["market_score"] == 2
    assert got["recommended_exposure"] == 0.60


def test_update_plan_run_status_with_error(repo):
    acc = _make_account(repo)
    created = repo.create_plan_run(
        run_key="rk-1", account_id=acc["id"], signal_date="2026-07-22",
        target_trade_date="2026-07-23", stock_pool_version_id=1,
        strategy_version_id=1, status="CREATED",
        account_snapshot_json={"cash": 100000}, data_snapshot_hash="s1",
    )
    repo.update_plan_run_status(created["id"], "FAILED", error={"code": "X"})
    got = repo.get_plan_run(created["id"])
    assert got["status"] == "FAILED"


def test_supersede_plan_run(repo):
    acc = _make_account(repo)
    old = repo.create_plan_run(
        run_key="rk-1", account_id=acc["id"], signal_date="2026-07-22",
        target_trade_date="2026-07-23", stock_pool_version_id=1,
        strategy_version_id=1, status="READY",
        account_snapshot_json={"cash": 100000}, data_snapshot_hash="s1",
    )
    new = repo.create_plan_run(
        run_key="rk-2", account_id=acc["id"], signal_date="2026-07-22",
        target_trade_date="2026-07-23", stock_pool_version_id=1,
        strategy_version_id=1, status="READY",
        account_snapshot_json={"cash": 90000}, data_snapshot_hash="s2",
    )
    repo.supersede_plan_run(old["id"], new["id"])
    got_old = repo.get_plan_run(old["id"])
    assert got_old["status"] == "SUPERSEDED"
    assert got_old["superseded_by_id"] == new["id"]


def test_publish_plan_run(repo):
    acc = _make_account(repo)
    created = repo.create_plan_run(
        run_key="rk-1", account_id=acc["id"], signal_date="2026-07-22",
        target_trade_date="2026-07-23", stock_pool_version_id=1,
        strategy_version_id=1, status="READY",
        account_snapshot_json={"cash": 100000}, data_snapshot_hash="s1",
    )
    repo.publish_plan_run(created["id"])
    got = repo.get_plan_run(created["id"])
    assert got["status"] == "PUBLISHED"
    assert got["published_at"] is not None


# ---- Plan item methods ----

def test_create_plan_item(repo):
    acc = _make_account(repo)
    run = repo.create_plan_run(
        run_key="rk-1", account_id=acc["id"], signal_date="2026-07-22",
        target_trade_date="2026-07-23", stock_pool_version_id=1,
        strategy_version_id=1, status="READY",
        account_snapshot_json={"cash": 100000}, data_snapshot_hash="s1",
    )
    item = repo.create_plan_item(
        plan_run_id=run["id"], stock_code="000001.SZ", action="CONDITIONAL_BUY",
        score=82.5, rank_no=1, trigger_price=10.21, do_not_chase_price=10.52,
        stop_price=9.5, target_2r_price=11.0, suggested_quantity=500,
        suggested_position_pct=0.05, risk_amount=350, risk_pct=0.0035,
        rule_hits_json=["TREND_UP", "NEAR_20D_HIGH"],
        rule_misses_json=[], invalidation_reason="开盘价 > 10.52 则取消",
    )
    assert item["id"] > 0
    assert item["action"] == "CONDITIONAL_BUY"


def test_get_plan_items_parses_json(repo):
    """get_plan_items 必须把 rule_hits_json/rule_misses_json/invalidation_reason 解析回 list/原值。"""
    acc = _make_account(repo)
    run = repo.create_plan_run(
        run_key="rk-1", account_id=acc["id"], signal_date="2026-07-22",
        target_trade_date="2026-07-23", stock_pool_version_id=1,
        strategy_version_id=1, status="READY",
        account_snapshot_json={"cash": 100000}, data_snapshot_hash="s1",
    )
    repo.create_plan_item(
        plan_run_id=run["id"], stock_code="000001.SZ", action="CONDITIONAL_BUY",
        score=82.5, rank_no=1, trigger_price=10.21,
        rule_hits_json=["TREND_UP", "NEAR_20D_HIGH"],
        rule_misses_json=["NO_GAP"],
        invalidation_reason="开盘价 > 10.52 则取消",
    )
    repo.create_plan_item(
        plan_run_id=run["id"], stock_code="600000.SH", action="WATCH",
        rule_hits_json=[], rule_misses_json=["SCORE_BELOW_70"],
    )
    items = repo.get_plan_items(run["id"])
    assert len(items) == 2
    # 第一个(按插入顺序或 stock_code,反正在里面)
    sz = next(i for i in items if i["stock_code"] == "000001.SZ")
    assert sz["rule_hits"] == ["TREND_UP", "NEAR_20D_HIGH"]
    assert sz["rule_misses"] == ["NO_GAP"]
    assert sz["invalidation_reason"] == "开盘价 > 10.52 则取消"
    assert sz["score"] == 82.5
    sh = next(i for i in items if i["stock_code"] == "600000.SH")
    assert sh["rule_hits"] == []
    assert sh["rule_misses"] == ["SCORE_BELOW_70"]


def test_get_plan_items_empty(repo):
    acc = _make_account(repo)
    run = repo.create_plan_run(
        run_key="rk-1", account_id=acc["id"], signal_date="2026-07-22",
        target_trade_date="2026-07-23", stock_pool_version_id=1,
        strategy_version_id=1, status="READY",
        account_snapshot_json={"cash": 100000}, data_snapshot_hash="s1",
    )
    assert repo.get_plan_items(run["id"]) == []
