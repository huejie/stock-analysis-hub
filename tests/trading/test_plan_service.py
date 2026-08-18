"""PlanService 集成测试(spec §19 验收场景 + 状态机 + 幂等键 + 发布)。

覆盖:
- 场景 A 正常生成:完整数据 → READY,CONDITIONAL_BUY 有触发价/止损/数量
- 场景 B 持仓行情缺失:BLOCKED,不生成新开仓
- 场景 D 账户回撤暂停:候选变 FORBIDDEN(DRAWDOWN_PAUSE)
- 场景 E 幂等:同输入复用;池变化 → 新计划 + 旧 SUPERSEDED
- 状态机:CREATED→VALIDATING→GENERATING→READY 可观测
- 发布:READY→PUBLISHED;重复发布报错;发布 BLOCKED 报错
- 策略未激活:StrategyNotActiveError
- 持仓优先:持仓项排在候选项之前(spec §8.1)
- get_plan_detail 形状
"""
import os
from datetime import date, datetime, timedelta, timezone
from threading import Event, Thread, current_thread

import pytest

from backend.trading.domain import DailyBar
from backend.trading.errors import (
    PlanAlreadyPublishedError,
    PlanBlockedError,
    PlanSupersededError,
    StrategyNotActiveError,
    TradingError,
)
from backend.trading.migrations import run_migrations
from backend.trading.repository import TradingRepository
from backend.trading.services.market_data_service import MarketDataService
from backend.trading.services.plan_service import PlanService
from backend.trading.services.portfolio_service import PortfolioService
from backend.trading.services.strategy_service import StrategyService
from backend.trading.strategies.market_regime import MarketRegimeResult

TEST_DB = "data/test_plan_svc.db"
SIGNAL_DATE = date(2026, 7, 22)        # 周三
TARGET_TRADE_DATE = date(2026, 7, 23)  # 周四
BENCHMARKS = ["000300.SH", "000905.SH"]
BENCHMARK = BENCHMARKS[0]


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def setup():
    """通用脚手架:临时 DB + 账户 + ACTIVE 策略 + 股票池(无行情)。

    各测试用 _gen_bars / _add_bars 自行注入行情。
    yield: (repo, md_svc, pf_svc, plan_svc, account, strategy, pool)
    """
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    repo = TradingRepository(TEST_DB)

    account = repo.create_account(
        name="main", initial_equity=100_000, cash_balance=100_000,
    )
    # 策略:创建并激活(Phase 5 真门禁需合格回测)
    strat_svc = StrategyService(repo)
    created = strat_svc.create_strategy(
        strategy_code="default", name="v1",
        params_json={"risk_per_trade": 0.005, "min_score": 70},
    )
    _seed_passing_backtest(repo, created["id"])
    strat_svc.activate_strategy(created["id"])
    strategy = repo.get_strategy(created["id"])

    pool = repo.create_stock_pool_version(
        pool_name="default",
        items=[
            {"stock_code": "000001.SZ", "stock_name": "平安银行", "sector_name": "银行"},
            {"stock_code": "600519.SH", "stock_name": "贵州茅台", "sector_name": "白酒"},
        ],
        source="text",
    )

    md_svc = MarketDataService(repo, provider=None)
    pf_svc = PortfolioService(repo)
    plan_svc = PlanService(repo, md_svc, pf_svc, benchmark_codes=BENCHMARKS)

    yield repo, md_svc, pf_svc, plan_svc, account, strategy, pool

    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


# ===================================================================
# 行情生成辅助(升序、防未来函数、使 regime=ATTACK 且评分>=70)
# ===================================================================

def _gen_bars(code, end_date, n, start_price, slope=0.004,
              hi_mult=1.02, lo_mult=0.98, volume=1_000_000):
    """生成 n 根升序 K 线,日期为 end_date 往前的连续 n 个自然日。

    收盘价按 slope 复利上升(制造趋势);high/low 为 close 的 ±2%(使 ATR/close
    落在 2%-6%,且 high_20d 距 close <= 2% → NEAR_20D_HIGH)。
    所有日期 <= end_date(防未来函数)。
    """
    bars = []
    for i in range(n):
        d = end_date - timedelta(days=n - 1 - i)
        close = round(start_price * ((1 + slope) ** i), 4)
        prev_close = round(start_price * ((1 + slope) ** (i - 1)), 4) if i > 0 else close
        bars.append(DailyBar(
            code=code, trade_date=d,
            open=prev_close,
            high=round(close * hi_mult, 4),
            low=round(close * lo_mult, 4),
            close=close,
            volume=volume, source="test",
        ))
    return bars


def _add_benchmark(repo, end_date=SIGNAL_DATE, n=70, start=3000.0):
    for code in BENCHMARKS:
        repo.upsert_daily_bars(_gen_bars(code, end_date, n, start, slope=0.003))


def _add_pool_stock(repo, code, end_date=SIGNAL_DATE, n=65, start=10.0):
    repo.upsert_daily_bars(_gen_bars(code, end_date, n, start, slope=0.004))


def _seed_passing_backtest(repo, version_id):
    """注入一条合格回测,供 Phase 5 激活门禁通过。"""
    created = repo.create_backtest_run(
        job_id=9000 + version_id, strategy_version_id=version_id,
        stock_pool_version_id=1, start_date="2026-01-01",
        end_date="2026-06-30", initial_equity=100000,
        fee_params_json={"commission_rate": 0.0003}, status="RUNNING",
    )
    repo.update_backtest_run(
        created["id"], status="SUCCEEDED",
        metrics_json={
            "trade_count": 100, "expectancy": 0.5, "profit_factor": 1.5,
            "max_drawdown": 0.1, "concentration": {
                "max_stock_share": 0.3, "max_month_share": 0.3,
                "concentrated_stock": False, "concentrated_month": False,
            },
        },
    )


# ===================================================================
# 场景 A:正常生成
# ===================================================================

def test_scenario_a_normal_generation(setup):
    """完整数据 → READY,CONDITIONAL_BUY 项有触发价/止损/数量/2R。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)

    result = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    assert result["status"] in ("READY", "PARTIAL")
    assert result["reused"] is False
    assert result["signal_date"] == SIGNAL_DATE.isoformat()
    assert result["target_trade_date"] == TARGET_TRADE_DATE.isoformat()
    assert len(result["run_key"]) == 16

    detail = plan_svc.get_plan_detail(result["id"])
    assert detail["market_regime"] == "ATTACK"  # M1+M2=2, breadth 降级不贡献
    assert detail["market_score"] >= 2

    buys = [i for i in detail["items"] if i["action"] == "CONDITIONAL_BUY"]
    assert len(buys) >= 1, "至少应有一个 CONDITIONAL_BUY"
    for item in buys:
        assert item["trigger_price"] is not None
        assert item["do_not_chase_price"] is not None
        assert item["stop_price"] is not None
        assert item["target_2r_price"] is not None
        assert item["suggested_quantity"] >= 100  # A 股最小手
        assert item["suggested_position_pct"] > 0
        assert item["risk_amount"] > 0
        assert "ENTRY_CONDITIONAL_BUY" in item["rule_hits"]
        # 2R > 触发价 > 止损价(几何关系正确)
        assert item["target_2r_price"] > item["trigger_price"]
        assert item["trigger_price"] > item["stop_price"]
        # 总仓位不超 ATTACK 上限 60%
        assert item["suggested_position_pct"] <= 0.60


def test_scenario_a_partial_when_degraded(setup):
    """breadth 降级 → warnings 非空 → status=PARTIAL。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)

    result = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    # 降级模式必然有 warning → PARTIAL
    assert result["status"] == "PARTIAL"
    assert any("降级" in w for w in result["warnings"])

    stored = repo.get_plan_run(result["id"])
    assert stored["degraded"] is True
    assert stored["warnings"] == result["warnings"]

    detail = plan_svc.get_plan_detail(result["id"])
    assert detail["degraded"] is True
    assert "市场宽度数据不可用" in detail["warnings"][0]

    reused = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    assert reused["reused"] is True
    assert reused["warnings"] == detail["warnings"]


def test_concurrent_generations_persist_call_local_quality(setup, monkeypatch):
    """同一 PlanService 交错生成时,每个 run 只持久化本调用的质量结果。"""
    repo, md_svc, pf_svc, plan_svc, account_a, strategy, pool = setup
    account_b = repo.create_account(
        name="secondary", initial_equity=200_000, cash_balance=200_000,
        is_active=False,
    )
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)

    quality_by_thread = {
        "plan-a": MarketRegimeResult(
            score=2, regime="ATTACK", degraded=True,
            recommended_exposure=0.60, m_signals={"test": "a"},
        ),
        "plan-b": MarketRegimeResult(
            score=-1, regime="DEFENSE", degraded=False,
            recommended_exposure=0.20, m_signals={"test": "b"},
        ),
    }

    def controlled_market_score(**kwargs):
        return quality_by_thread[current_thread().name]

    monkeypatch.setattr(
        "backend.trading.services.plan_service.compute_market_score",
        controlled_market_score,
    )

    original_generate_items = plan_svc._generate_items
    a_generated = Event()
    b_generated = Event()
    release_a = Event()

    def interleaved_generate_items(**kwargs):
        result = original_generate_items(**kwargs)
        if current_thread().name == "plan-a":
            a_generated.set()
            if not b_generated.wait(5) or not release_a.wait(5):
                raise TimeoutError("plan A interleaving timed out")
        else:
            if not a_generated.wait(5):
                raise TimeoutError("plan B interleaving timed out")
            b_generated.set()
        return result

    monkeypatch.setattr(plan_svc, "_generate_items", interleaved_generate_items)

    results = {}
    errors = {}

    def generate(label, account_id):
        try:
            results[label] = plan_svc.generate_plan(
                account_id=account_id, signal_date=SIGNAL_DATE,
                stock_pool_version_id=pool["id"],
                strategy_version_id=strategy["id"],
            )
        except BaseException as exc:
            errors[label] = exc

    thread_a = Thread(
        target=generate, args=("a", account_a["id"]), name="plan-a"
    )
    thread_b = Thread(
        target=generate, args=("b", account_b["id"]), name="plan-b"
    )
    thread_a.start()
    assert a_generated.wait(5), "plan A did not reach controlled interleaving"
    thread_b.start()
    thread_b.join(5)
    assert not thread_b.is_alive(), "plan B did not complete"
    release_a.set()
    thread_a.join(5)
    assert not thread_a.is_alive(), "plan A did not complete"
    assert errors == {}

    detail_a = plan_svc.get_plan_detail(results["a"]["id"])
    detail_b = plan_svc.get_plan_detail(results["b"]["id"])
    assert {
        "regime": detail_a["market_regime"],
        "score": detail_a["market_score"],
        "exposure": detail_a["recommended_exposure"],
        "degraded": detail_a["degraded"],
        "warnings": detail_a["warnings"],
    } == {
        "regime": "ATTACK", "score": 2, "exposure": 0.60,
        "degraded": True,
        "warnings": [
            "市场宽度数据不可用,降级模式(degraded=True,新开仓风险减半)"
        ],
    }
    assert {
        "regime": detail_b["market_regime"],
        "score": detail_b["market_score"],
        "exposure": detail_b["recommended_exposure"],
        "degraded": detail_b["degraded"],
        "warnings": detail_b["warnings"],
    } == {
        "regime": "DEFENSE", "score": -1, "exposure": 0.20,
        "degraded": False, "warnings": [],
    }


# ===================================================================
# 场景 B:持仓行情缺失 → BLOCKED
# ===================================================================

def test_scenario_b_position_missing_blocked(setup):
    """持仓在 signal_date 缺行情 → BLOCKED,不生成新开仓,抛 PlanBlockedError。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    # 持仓 600000.SH,但只插入到 signal_date 前 3 天(信号日缺失)
    repo.upsert_position(
        account_id=account["id"], stock_code="600000.SH",
        quantity=1000, available_quantity=1000, average_cost=10.0,
        initial_stop=9.0,
    )
    repo.upsert_daily_bars(
        _gen_bars("600000.SH", SIGNAL_DATE - timedelta(days=3), 30, 10.0)
    )

    with pytest.raises(PlanBlockedError) as ei:
        plan_svc.generate_plan(
            account_id=account["id"], signal_date=SIGNAL_DATE,
            stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
        )
    assert "600000.SH" in ei.value.details["missing"]

    # run 已落库为 BLOCKED,且无 items
    runs = repo.list_plan_runs(signal_date=SIGNAL_DATE.isoformat())
    assert len(runs) == 1
    assert runs[0]["status"] == "BLOCKED"
    assert runs[0]["degraded"] is False
    assert runs[0]["warnings"] == []
    assert repo.get_plan_items(runs[0]["id"]) == []


def test_scenario_b_records_data_issue(setup):
    """BLOCKED 时持仓缺失写入 trade_data_issues(可追溯)。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    repo.upsert_position(
        account_id=account["id"], stock_code="600000.SH",
        quantity=1000, available_quantity=1000, average_cost=10.0,
    )
    # 不给 600000.SH 任何 signal_date 行情
    try:
        plan_svc.generate_plan(
            account_id=account["id"], signal_date=SIGNAL_DATE,
            stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
        )
    except PlanBlockedError:
        pass
    issues = repo.list_data_issues(trade_date=SIGNAL_DATE.isoformat(),
                                   severity="BLOCKING")
    codes = {i["stock_code"] for i in issues}
    assert "600000.SH" in codes


# ===================================================================
# 场景 D:账户回撤暂停 → 候选 FORBIDDEN
# ===================================================================

def test_scenario_d_drawdown_pause_forbids_candidates(setup):
    """回撤 >= 阈值 → 新候选变 FORBIDDEN(DRAWDOWN_PAUSE),持仓建议不受影响。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    # 建一个高 peak 的历史快照,使当前 equity(100000)回撤 >= 8%
    # drawdown = (120000 - 100000)/120000 = 16.7% >= 8%
    repo.upsert_equity_snapshot(
        account_id=account["id"], trade_date=(SIGNAL_DATE - timedelta(days=1)).isoformat(),
        cash=100_000, market_value=20_000, total_equity=120_000,
        exposure=0.167, peak_equity=120_000, drawdown=0.0,
    )

    result = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    detail = plan_svc.get_plan_detail(result["id"])
    # 所有候选(池内股票)应为 FORBIDDEN
    pool_codes = {"000001.SZ", "600519.SH"}
    candidate_items = [i for i in detail["items"] if i["stock_code"] in pool_codes]
    assert len(candidate_items) == 2
    for item in candidate_items:
        assert item["action"] == "FORBIDDEN"
        assert "DRAWDOWN_PAUSE" in item["rule_misses"]
    # 无 CONDITIONAL_BUY
    assert not any(i["action"] == "CONDITIONAL_BUY" for i in detail["items"])
    # warning 提示回撤
    assert any("回撤" in w for w in result["warnings"])


def test_position_count_limit_forbids_excess(setup):
    """持仓数达到 max_positions(5)时,新候选触发 POSITION_COUNT_EXCEEDED → FORBIDDEN。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    # 建满 5 只持仓(default max_positions=5)
    for i, code in enumerate(["600000.SH", "600036.SH", "601318.SH",
                              "600028.SH", "601628.SH"]):
        repo.upsert_position(
            account_id=account["id"], stock_code=code,
            quantity=1000, available_quantity=1000, average_cost=10.0,
            initial_stop=9.0,
        )
        repo.upsert_daily_bars(_gen_bars(code, SIGNAL_DATE, 65, 10.0))

    r = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    detail = plan_svc.get_plan_detail(r["id"])
    # 池内候选(不属已持仓)应被仓位数量限制 → WATCH(NO_PORTFOLIO_CAPACITY)
    # 或 FORBIDDEN(POSITION_COUNT_EXCEEDED)。无论如何不应有 CONDITIONAL_BUY。
    pool_candidates = [i for i in detail["items"]
                       if i["stock_code"] in ("000001.SZ", "600519.SH")]
    assert pool_candidates, "池内候选应被评估"
    for item in pool_candidates:
        assert item["action"] != "CONDITIONAL_BUY"
    assert not any(i["action"] == "CONDITIONAL_BUY" for i in detail["items"])


# ===================================================================
# 场景 E:幂等
# ===================================================================

def test_scenario_e_idempotency_reuse(setup):
    """相同输入二次生成 → 同一 run_id,reused=True。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)

    r1 = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    r2 = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    assert r1["id"] == r2["id"]
    assert r1["run_key"] == r2["run_key"]
    assert r1["reused"] is False
    assert r2["reused"] is True
    # 只有一个 run
    assert len(repo.list_plan_runs(signal_date=SIGNAL_DATE.isoformat())) == 1


class _PausedPlanOwnerRepository:
    def __init__(self, inner):
        self._inner = inner
        self.entered = Event()
        self.release = Event()
        self.status_writes = []
        self.item_writes = 0

    def transition_plan_run_status(
        self, run_id, status, *, expected_statuses, generation_owner_id, now,
        error=None,
    ):
        if status == "VALIDATING":
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise TimeoutError("plan owner was not released")
        self.status_writes.append((run_id, status))
        return self._inner.transition_plan_run_status(
            run_id,
            status,
            expected_statuses=expected_statuses,
            generation_owner_id=generation_owner_id,
            now=now,
            error=error,
        )

    def create_plan_item(self, **kwargs):
        self.item_writes += 1
        return self._inner.create_plan_item(**kwargs)

    def finalize_plan_run(self, run_id, **kwargs):
        self.item_writes += len(kwargs["items"])
        return self._inner.finalize_plan_run(run_id, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _CountingPlanRepository:
    def __init__(self, inner):
        self._inner = inner
        self.status_writes = []
        self.item_writes = 0

    def transition_plan_run_status(
        self, run_id, status, *, expected_statuses, generation_owner_id, now,
        error=None,
    ):
        self.status_writes.append((run_id, status))
        return self._inner.transition_plan_run_status(
            run_id,
            status,
            expected_statuses=expected_statuses,
            generation_owner_id=generation_owner_id,
            now=now,
            error=error,
        )

    def create_plan_item(self, **kwargs):
        self.item_writes += 1
        return self._inner.create_plan_item(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_two_services_reuse_in_progress_run_without_duplicate_writes(setup):
    repo, _, _, _, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)

    owner_repo = _PausedPlanOwnerRepository(TradingRepository(TEST_DB))
    reused_repo = _CountingPlanRepository(TradingRepository(TEST_DB))
    owner_service = PlanService(
        owner_repo,
        MarketDataService(owner_repo, provider=None),
        PortfolioService(owner_repo),
        benchmark_codes=BENCHMARKS,
    )
    reused_service = PlanService(
        reused_repo,
        MarketDataService(reused_repo, provider=None),
        PortfolioService(reused_repo),
        benchmark_codes=BENCHMARKS,
    )
    kwargs = dict(
        account_id=account["id"],
        signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"],
        strategy_version_id=strategy["id"],
    )
    results = {}
    errors = {}

    def generate_owner():
        try:
            results["owner"] = owner_service.generate_plan(**kwargs)
        except BaseException as exc:
            errors["owner"] = exc

    thread = Thread(target=generate_owner, name="plan-owner")
    thread.start()
    assert owner_repo.entered.wait(timeout=5), "owner did not create the run"
    try:
        results["reused"] = reused_service.generate_plan(**kwargs)
    except BaseException as exc:
        errors["reused"] = exc
    finally:
        owner_repo.release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert errors == {}
    assert results["owner"]["id"] == results["reused"]["id"]
    assert results["owner"]["reused"] is False
    assert results["reused"]["reused"] is True
    assert results["reused"]["status"] == "CREATED"
    assert reused_repo.status_writes == []
    assert reused_repo.item_writes == 0
    assert len(repo.get_plan_items(results["owner"]["id"])) == owner_repo.item_writes


@pytest.mark.parametrize("status", ["CREATED", "VALIDATING", "GENERATING"])
def test_in_progress_state_matrix_reuses_without_writes(setup, status):
    repo, _, _, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    kwargs = {
        "account_id": account["id"],
        "signal_date": SIGNAL_DATE,
        "stock_pool_version_id": pool["id"],
        "strategy_version_id": strategy["id"],
    }
    created = plan_svc.generate_plan(**kwargs)
    repo.update_plan_run_status(created["id"], status)
    lease_now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert repo.acquire_plan_generation_lease(
        created["id"],
        owner_id="still-running",
        now=lease_now,
        expires_at=lease_now + timedelta(minutes=1),
    )
    counting_repo = _CountingPlanRepository(TradingRepository(TEST_DB))
    reused_service = PlanService(
        counting_repo,
        MarketDataService(counting_repo, provider=None),
        PortfolioService(counting_repo),
        benchmark_codes=BENCHMARKS,
        generation_now_fn=lambda: lease_now,
    )

    reused = reused_service.generate_plan(**kwargs)

    assert reused["id"] == created["id"]
    assert reused["status"] == status
    assert reused["reused"] is True
    assert counting_repo.status_writes == []
    assert counting_repo.item_writes == 0


@pytest.mark.parametrize("status", ["READY", "PARTIAL", "PUBLISHED"])
def test_success_terminal_state_matrix_reuses_existing_run(setup, status):
    repo, _, _, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    kwargs = {
        "account_id": account["id"],
        "signal_date": SIGNAL_DATE,
        "stock_pool_version_id": pool["id"],
        "strategy_version_id": strategy["id"],
    }
    created = plan_svc.generate_plan(**kwargs)
    repo.update_plan_run_status(created["id"], status)

    reused = plan_svc.generate_plan(**kwargs)

    assert reused["id"] == created["id"]
    assert reused["status"] == status
    assert reused["reused"] is True


def test_scenario_e_new_pool_supersedes_old(setup):
    """股票池变化 → 新计划,旧计划 SUPERSEDED。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)

    r1 = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )

    # 新池版本(多一只股票)→ items_hash 变 → pool_version_hash 变 → 新 run_key
    pool2 = repo.create_stock_pool_version(
        pool_name="default",
        items=[
            {"stock_code": "000001.SZ", "stock_name": "平安银行"},
            {"stock_code": "600519.SH", "stock_name": "贵州茅台"},
            {"stock_code": "000333.SZ", "stock_name": "美的集团"},
        ],
        source="text",
    )
    _add_pool_stock(repo, "000333.SZ", start=50.0)

    r2 = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool2["id"], strategy_version_id=strategy["id"],
    )
    assert r2["reused"] is False
    assert r1["id"] != r2["id"]

    # 旧 run 被 supersede
    old = repo.get_plan_run(r1["id"])
    assert old["status"] == "SUPERSEDED"
    assert old["superseded_by_id"] == r2["id"]


def test_force_new_version_always_creates(setup):
    """force_new_version=True → 即使同 run_key 也强制新建 + supersede 旧 READY。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)

    r1 = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    r2 = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
        force_new_version=True,
    )
    assert r2["reused"] is False
    assert r1["id"] != r2["id"]
    # 同 signal_date 旧 READY 被 supersede
    old = repo.get_plan_run(r1["id"])
    assert old["status"] == "SUPERSEDED"


def test_concurrent_force_new_generation_fences_superseded_peer(setup, monkeypatch):
    """被同账户新版本替代的旧 owner 不得把自己写回可执行终态。"""
    repo, _, _, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    base = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )

    generated_by_a = Event()
    release_a = Event()
    original_generate_items = plan_svc._generate_items

    def controlled_generate_items(**kwargs):
        result = original_generate_items(**kwargs)
        if current_thread().name == "force-a":
            generated_by_a.set()
            assert release_a.wait(timeout=5), "force-A was not released"
        return result

    monkeypatch.setattr(plan_svc, "_generate_items", controlled_generate_items)
    monkeypatch.setattr(
        plan_svc,
        "_derive_force_run_key",
        lambda base_key: f"{base_key}-force-{current_thread().name}",
    )
    kwargs = {
        "account_id": account["id"],
        "signal_date": SIGNAL_DATE,
        "stock_pool_version_id": pool["id"],
        "strategy_version_id": strategy["id"],
        "force_new_version": True,
    }
    outcomes, errors = {}, {}

    def generate(label):
        try:
            outcomes[label] = plan_svc.generate_plan(**kwargs)
        except BaseException as exc:
            errors[label] = exc

    owner = Thread(target=generate, args=("a",), name="force-a")
    owner.start()
    assert generated_by_a.wait(timeout=5), "force-A did not reach finalization"
    peer = Thread(target=generate, args=("b",), name="force-b")
    peer.start()
    peer.join(timeout=5)
    assert not peer.is_alive(), "force-B did not finish"
    release_a.set()
    owner.join(timeout=5)
    assert not owner.is_alive(), "force-A did not finish"

    runs = repo.list_plan_runs(
        signal_date=SIGNAL_DATE.isoformat(), account_id=account["id"]
    )
    executable = [
        run for run in runs
        if run["status"] in ("READY", "PARTIAL", "PUBLISHED")
    ]
    assert len(executable) == 1
    assert executable[0]["id"] != base["id"]
    force_a = next(run for run in runs if run["run_key"].endswith("force-force-a"))
    assert force_a["status"] == "SUPERSEDED"
    assert isinstance(errors.get("a"), PlanSupersededError)


def test_force_new_never_creates_second_executable_plan_beside_published(setup):
    """发布是不可替代的快照；后续强制生成必须被围栏为 SUPERSEDED。"""
    repo, _, _, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    published = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    plan_svc.publish_plan(published["id"])

    with pytest.raises(PlanSupersededError):
        plan_svc.generate_plan(
            account_id=account["id"], signal_date=SIGNAL_DATE,
            stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
            force_new_version=True,
        )

    runs = repo.list_plan_runs(
        signal_date=SIGNAL_DATE.isoformat(), account_id=account["id"]
    )
    assert [run["status"] for run in runs].count("PUBLISHED") == 1
    assert not any(run["status"] in ("READY", "PARTIAL") for run in runs)


class _PausedPublishRepository:
    def __init__(self, inner):
        self._inner = inner
        self.entered = Event()
        self.release = Event()

    def publish_plan_run(self, run_id):
        self.entered.set()
        assert self.release.wait(timeout=5), "publish was not released"
        return self._inner.publish_plan_run(run_id)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_stale_publish_cannot_revive_plan_fenced_by_new_finalization(setup):
    """publish 的读写之间被新终态替代时，旧写入必须 CAS 失败。"""
    repo, _, _, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    old = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    paused_repo = _PausedPublishRepository(repo)
    plan_svc.repo = paused_repo
    publish_errors = []

    def publish_old():
        try:
            plan_svc.publish_plan(old["id"])
        except BaseException as exc:
            publish_errors.append(exc)

    thread = Thread(target=publish_old, name="stale-publish")
    thread.start()
    assert paused_repo.entered.wait(timeout=5), "publish did not reach CAS"
    plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
        force_new_version=True,
    )
    paused_repo.release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    assert len(publish_errors) == 1
    assert isinstance(publish_errors[0], PlanSupersededError)
    executable = [
        run for run in repo.list_plan_runs(
            signal_date=SIGNAL_DATE.isoformat(), account_id=account["id"]
        )
        if run["status"] in ("READY", "PARTIAL", "PUBLISHED")
    ]
    assert len(executable) == 1


def test_generation_lease_does_not_steal_live_owner_and_recovers_stale_run(
    setup,
):
    """短 lease 下活 owner 保持独占，过期 owner 原子保留为 SUPERSEDED。"""
    repo, _, _, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    kwargs = {
        "account_id": account["id"],
        "signal_date": SIGNAL_DATE,
        "stock_pool_version_id": pool["id"],
        "strategy_version_id": strategy["id"],
    }
    old = plan_svc.generate_plan(**kwargs)
    repo.update_plan_run_status(old["id"], "GENERATING")
    start = datetime(2026, 7, 22, 9, 30, tzinfo=timezone.utc)
    assert repo.acquire_plan_generation_lease(
        old["id"], owner_id="crashed-owner", now=start,
        expires_at=start + timedelta(seconds=1),
    )

    live = PlanService(
        repo, MarketDataService(repo, provider=None), PortfolioService(repo),
        benchmark_codes=BENCHMARKS,
        generation_now_fn=lambda: start,
        generation_lease_seconds=1,
    ).generate_plan(**kwargs)
    assert live["id"] == old["id"]
    assert live["status"] == "GENERATING"
    assert live["reused"] is True
    assert len(repo.list_plan_runs()) == 1

    recovered = PlanService(
        repo, MarketDataService(repo, provider=None), PortfolioService(repo),
        benchmark_codes=BENCHMARKS,
        generation_now_fn=lambda: start + timedelta(seconds=2),
        generation_lease_seconds=1,
    ).generate_plan(**kwargs)
    assert recovered["id"] != old["id"]
    assert recovered["status"] in ("READY", "PARTIAL")
    assert repo.get_plan_run(old["id"])["status"] == "SUPERSEDED"


class _PostTransactionRecoveryRepository:
    """Makes the recovery transaction observe a newer terminal plan state."""

    def __init__(self, inner):
        self._inner = inner

    def recover_stale_plan_run_and_create_successor(self, **kwargs):
        self._inner.update_plan_run_status(kwargs["stale_run_id"], "READY")
        return self._inner.recover_stale_plan_run_and_create_successor(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_recovery_loser_resolves_post_transaction_plan_status(setup):
    """恢复 loser 必须使用事务内重新读取的终态，不可返回旧 GENERATING 快照。"""
    repo, _, _, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    kwargs = {
        "account_id": account["id"],
        "signal_date": SIGNAL_DATE,
        "stock_pool_version_id": pool["id"],
        "strategy_version_id": strategy["id"],
    }
    existing = plan_svc.generate_plan(**kwargs)
    repo.update_plan_run_status(existing["id"], "GENERATING")

    recovering_repo = _PostTransactionRecoveryRepository(repo)
    result = PlanService(
        recovering_repo,
        MarketDataService(recovering_repo, provider=None),
        PortfolioService(recovering_repo),
        benchmark_codes=BENCHMARKS,
    ).generate_plan(**kwargs)

    assert result["id"] == existing["id"]
    assert result["status"] == "READY"
    assert result["reused"] is True


def test_empty_benchmark_configuration_is_explicit_degraded_plan_mode(setup):
    """None 才选默认基准；[] 不取默认且不削弱股票池门禁。"""
    repo, md_svc, pf_svc, _, account, strategy, pool = setup
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)

    assert md_svc.update_benchmark(SIGNAL_DATE, benchmark_codes=[]) == 0
    health = md_svc.check_data_health(
        trade_date=SIGNAL_DATE, pool_name="default", benchmark_codes=[]
    )
    assert health["benchmark_codes"] == []
    assert health["overall_status"] == "OK"

    result = PlanService(
        repo, md_svc, pf_svc, benchmark_codes=[]
    ).generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    detail = repo.get_plan_run(result["id"])
    assert result["status"] == "PARTIAL"
    assert detail["degraded"] is True
    assert detail["market_regime"] == "NEUTRAL"
    assert any("未配置市场基准" in warning for warning in detail["warnings"])


def test_blocked_run_reuses_terminal_until_force_new(setup):
    """BLOCKED 普通重试稳定重放原错误；显式 force 才新建。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    # 持仓缺行情 → BLOCKED
    repo.upsert_position(
        account_id=account["id"], stock_code="600000.SH",
        quantity=1000, available_quantity=1000, average_cost=10.0,
    )
    with pytest.raises(PlanBlockedError) as blocked_error:
        plan_svc.generate_plan(
            account_id=account["id"], signal_date=SIGNAL_DATE,
            stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
        )
    blocked_id = blocked_error.value.details["run_id"]
    # 补上持仓行情后，普通重试仍返回已持久化的 BLOCKED，不原地重写。
    repo.upsert_daily_bars(_gen_bars("600000.SH", SIGNAL_DATE, 65, 10.0))
    with pytest.raises(PlanBlockedError) as replayed:
        plan_svc.generate_plan(
            account_id=account["id"], signal_date=SIGNAL_DATE,
            stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
        )
    assert replayed.value.message == blocked_error.value.message
    assert replayed.value.details == blocked_error.value.details
    assert replayed.value.details["run_id"] == blocked_id
    assert repo.get_plan_items(blocked_id) == []

    r3 = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
        force_new_version=True,
    )
    assert r3["id"] != blocked_id
    assert r3["status"] in ("READY", "PARTIAL")
    assert r3["reused"] is False


def test_failed_run_replays_stable_error_until_force_new(setup, monkeypatch):
    repo, _, _, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)

    def fail_generation(**_kwargs):
        raise RuntimeError("deterministic engine failure")

    monkeypatch.setattr(plan_svc, "_generate_items", fail_generation)
    kwargs = {
        "account_id": account["id"],
        "signal_date": SIGNAL_DATE,
        "stock_pool_version_id": pool["id"],
        "strategy_version_id": strategy["id"],
    }
    with pytest.raises(TradingError) as first:
        plan_svc.generate_plan(**kwargs)
    with pytest.raises(TradingError) as replayed:
        plan_svc.generate_plan(**kwargs)

    assert first.value.code == "PLAN_GENERATION_FAILED"
    assert replayed.value.code == "PLAN_GENERATION_FAILED"
    assert replayed.value.message == first.value.message
    assert replayed.value.details == first.value.details
    runs = repo.list_plan_runs(signal_date=SIGNAL_DATE.isoformat())
    assert len(runs) == 1
    assert runs[0]["status"] == "FAILED"
    assert runs[0]["error"]["code"] == "PLAN_GENERATION_FAILED"


def test_superseded_run_is_never_returned_as_successful_reuse(setup):
    repo, _, _, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    kwargs = {
        "account_id": account["id"],
        "signal_date": SIGNAL_DATE,
        "stock_pool_version_id": pool["id"],
        "strategy_version_id": strategy["id"],
    }
    created = plan_svc.generate_plan(**kwargs)
    repo.update_plan_run_status(created["id"], "SUPERSEDED")

    with pytest.raises(TradingError) as replayed:
        plan_svc.generate_plan(**kwargs)

    assert replayed.value.code == "PLAN_SUPERSEDED"
    assert replayed.value.details == {
        "run_id": created["id"],
        "run_key": created["run_key"],
        "status": "SUPERSEDED",
    }
    assert len(repo.list_plan_runs(signal_date=SIGNAL_DATE.isoformat())) == 1


def test_original_run_key_reuses_successful_recovery_successor(setup):
    """仅因 generation lease 超时而 supersede 的原 key 可重放其后继计划。"""
    repo, _, _, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    kwargs = {
        "account_id": account["id"],
        "signal_date": SIGNAL_DATE,
        "stock_pool_version_id": pool["id"],
        "strategy_version_id": strategy["id"],
    }
    original = plan_svc.generate_plan(**kwargs)
    repo.update_plan_run_status(original["id"], "GENERATING")
    started = datetime(2026, 7, 22, 9, 30, tzinfo=timezone.utc)
    assert repo.acquire_plan_generation_lease(
        original["id"], owner_id="crashed-owner", now=started,
        expires_at=started + timedelta(seconds=1),
    )
    recovery_service = PlanService(
        repo, MarketDataService(repo, provider=None), PortfolioService(repo),
        benchmark_codes=BENCHMARKS,
        generation_now_fn=lambda: started + timedelta(seconds=2),
        generation_lease_seconds=1,
    )
    recovered = recovery_service.generate_plan(**kwargs)

    original_row = repo.get_plan_run(original["id"])
    assert original_row["error"] == {
        "code": "PLAN_GENERATION_RECOVERED",
        "message": "generation owner lease expired; created successor plan",
        "details": {
            "reason": "GENERATION_LEASE_EXPIRED",
            "successor_run_id": recovered["id"],
            "original_run_key": original["run_key"],
        },
    }

    replayed = recovery_service.generate_plan(**kwargs)

    assert replayed["id"] == recovered["id"]
    assert replayed["status"] in ("READY", "PARTIAL")
    assert replayed["reused"] is True


def test_original_run_key_follows_consecutive_recovery_successors(setup):
    """从原 key 调用服务时，可再次恢复已过期的恢复后继。"""
    repo, _, _, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    kwargs = {
        "account_id": account["id"],
        "signal_date": SIGNAL_DATE,
        "stock_pool_version_id": pool["id"],
        "strategy_version_id": strategy["id"],
    }
    r0 = plan_svc.generate_plan(**kwargs)
    repo.update_plan_run_status(r0["id"], "GENERATING")
    started = datetime(2026, 7, 22, 9, 30, tzinfo=timezone.utc)
    assert repo.acquire_plan_generation_lease(
        r0["id"], owner_id="crashed-r0", now=started,
        expires_at=started + timedelta(seconds=1),
    )
    first_recovery = PlanService(
        repo, MarketDataService(repo, provider=None), PortfolioService(repo),
        benchmark_codes=BENCHMARKS,
        generation_now_fn=lambda: started + timedelta(seconds=2),
        generation_lease_seconds=1,
    ).generate_plan(**kwargs)
    r1 = repo.get_plan_run(first_recovery["id"])
    repo.update_plan_run_status(r1["id"], "GENERATING")
    assert repo.acquire_plan_generation_lease(
        r1["id"], owner_id="crashed-r1",
        now=started + timedelta(seconds=2),
        expires_at=started + timedelta(seconds=3),
    )

    replayed = PlanService(
        repo, MarketDataService(repo, provider=None), PortfolioService(repo),
        benchmark_codes=BENCHMARKS,
        generation_now_fn=lambda: started + timedelta(seconds=4),
        generation_lease_seconds=1,
    ).generate_plan(**kwargs)

    assert replayed["id"] not in (r0["id"], r1["id"])
    assert replayed["status"] in ("READY", "PARTIAL")
    assert replayed["reused"] is False
    assert repo.get_plan_run(r1["id"])["status"] == "SUPERSEDED"


# ===================================================================
# 状态机:CREATED→VALIDATING→GENERATING→READY 可观测
# ===================================================================

class _StatusCapturingRepo:
    """包装 repo,记录所有 update_plan_run_status 调用序列。"""

    def __init__(self, inner):
        self._inner = inner
        self.transitions: list[tuple[int, str]] = []

    def transition_plan_run_status(
        self, run_id, status, *, expected_statuses, generation_owner_id, now,
        error=None,
    ):
        self.transitions.append((run_id, status))
        return self._inner.transition_plan_run_status(
            run_id,
            status,
            expected_statuses=expected_statuses,
            generation_owner_id=generation_owner_id,
            now=now,
            error=error,
        )

    def finalize_plan_run(self, run_id, **kwargs):
        self.transitions.append((run_id, kwargs["status"]))
        return self._inner.finalize_plan_run(run_id, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_state_machine_transitions_observable(setup):
    """成功生成时状态序列包含 CREATED→VALIDATING→GENERATING→READY。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)

    cap = _StatusCapturingRepo(repo)
    plan_svc.repo = cap  # 注入捕获层

    result = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    run_id = result["id"]
    seq = [s for (rid, s) in cap.transitions if rid == run_id]
    # CREATED 由 create_plan_run 写入(不在 transitions 里),后续顺序:
    assert seq == ["VALIDATING", "GENERATING", result["status"]]
    assert result["status"] in ("READY", "PARTIAL")


def test_state_machine_blocked_terminal(setup):
    """数据门禁失败 → VALIDATING 后直接 BLOCKED(无 GENERATING)。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    # 持仓缺行情触发 BLOCKED
    repo.upsert_position(
        account_id=account["id"], stock_code="600000.SH",
        quantity=1000, available_quantity=1000, average_cost=10.0,
    )
    cap = _StatusCapturingRepo(repo)
    plan_svc.repo = cap

    with pytest.raises(PlanBlockedError):
        plan_svc.generate_plan(
            account_id=account["id"], signal_date=SIGNAL_DATE,
            stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
        )
    # 找到本次 run 的序列
    run_id = repo.list_plan_runs(signal_date=SIGNAL_DATE.isoformat())[0]["id"]
    seq = [s for (rid, s) in cap.transitions if rid == run_id]
    assert seq == ["VALIDATING", "BLOCKED"]


# ===================================================================
# 发布
# ===================================================================

def test_publish_ready_to_published(setup):
    """READY → PUBLISHED。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    r = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    pub = plan_svc.publish_plan(r["id"])
    assert pub["status"] == "PUBLISHED"
    assert pub["published_at"] is not None
    assert repo.get_plan_run(r["id"])["status"] == "PUBLISHED"


def test_publish_twice_raises(setup):
    """已发布计划重复发布 → PlanAlreadyPublishedError。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    r = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    plan_svc.publish_plan(r["id"])
    with pytest.raises(PlanAlreadyPublishedError):
        plan_svc.publish_plan(r["id"])


def test_publish_blocked_raises(setup):
    """BLOCKED 计划不可发布 → 报错。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    repo.upsert_position(
        account_id=account["id"], stock_code="600000.SH",
        quantity=1000, available_quantity=1000, average_cost=10.0,
    )
    with pytest.raises(PlanBlockedError):
        plan_svc.generate_plan(
            account_id=account["id"], signal_date=SIGNAL_DATE,
            stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
        )
    blocked_run = repo.list_plan_runs(signal_date=SIGNAL_DATE.isoformat())[0]
    with pytest.raises(PlanBlockedError):
        plan_svc.publish_plan(blocked_run["id"])


# ===================================================================
# 策略未激活
# ===================================================================

def test_strategy_not_active_raises(setup):
    """DRAFT 策略 → StrategyNotActiveError。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    # 再建一个 DRAFT 策略(不激活)
    draft = repo.create_strategy_version(
        strategy_code="default", name="v2-draft",
        params_json={"risk_per_trade": 0.005, "min_score": 75},  # 不同 params
    )
    with pytest.raises(StrategyNotActiveError):
        plan_svc.generate_plan(
            account_id=account["id"], signal_date=SIGNAL_DATE,
            stock_pool_version_id=pool["id"], strategy_version_id=draft["id"],
        )


def test_strategy_nonexistent_raises(setup):
    """不存在的策略版本 → ValueError。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    with pytest.raises(ValueError):
        plan_svc.generate_plan(
            account_id=account["id"], signal_date=SIGNAL_DATE,
            stock_pool_version_id=pool["id"], strategy_version_id=99999,
        )


# ===================================================================
# 持仓优先(spec §8.1 先处理持仓)
# ===================================================================

def test_positions_before_candidates_order(setup):
    """持仓项(HOLD/EXIT/REDUCE)在候选项之前,且持仓正常评估退出。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    # 一个健康持仓(上升趋势 → HOLD),有 signal_date 行情
    repo.upsert_position(
        account_id=account["id"], stock_code="600000.SH",
        quantity=1000, available_quantity=1000, average_cost=9.0,
        initial_stop=8.0,
    )
    repo.upsert_daily_bars(_gen_bars("600000.SH", SIGNAL_DATE, 65, 9.0))

    r = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    detail = plan_svc.get_plan_detail(r["id"])
    # 第一项应是持仓项
    assert detail["items"][0]["stock_code"] == "600000.SH"
    assert detail["items"][0]["action"] in ("HOLD", "EXIT", "REDUCE")
    # 池内候选项(000001.SZ / 600519.SH)排在持仓之后
    codes_in_order = [i["stock_code"] for i in detail["items"]]
    assert codes_in_order[0] == "600000.SH"


def test_position_exit_triggered(setup):
    """持仓跌破 MA60 → EXIT 项生成(退出规则接入)。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    # 持仓:先升后暴跌,使最新收盘 < MA60
    bars = _gen_bars("600000.SH", SIGNAL_DATE - timedelta(days=20), 45, 10.0)
    # 追加 20 根下跌 K 线到 signal_date
    crash = _gen_bars_descending("600000.SH", SIGNAL_DATE, 20,
                                 start=bars[-1].close, slope=0.03)
    repo.upsert_position(
        account_id=account["id"], stock_code="600000.SH",
        quantity=1000, available_quantity=1000, average_cost=10.0,
        initial_stop=7.0,
    )
    repo.upsert_daily_bars(bars + crash)

    r = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    detail = plan_svc.get_plan_detail(r["id"])
    pos_item = next(i for i in detail["items"] if i["stock_code"] == "600000.SH")
    assert pos_item["action"] == "EXIT"


# ===================================================================
# get_plan_detail 形状(spec §11.4)
# ===================================================================

def test_get_plan_detail_shape(setup):
    """get_plan_detail 返回 spec §11.4 关键字段。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    r = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    detail = plan_svc.get_plan_detail(r["id"])
    for key in ("id", "status", "signal_date", "target_trade_date",
                "market_regime", "market_score", "recommended_exposure",
                "warnings", "items"):
        assert key in detail, f"missing key {key}"
    assert detail["id"] == r["id"]
    assert detail["market_regime"] == "ATTACK"
    assert isinstance(detail["items"], list)
    assert len(detail["items"]) >= 1


def test_get_plan_detail_nonexistent(setup):
    """不存在的 run_id → None。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    assert plan_svc.get_plan_detail(99999) is None


def test_generate_plan_rejects_requested_unusable_pool_version(setup):
    """显式请求已失效的污染股池时必须 fail closed。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    assert repo.mark_stock_pool_unusable(
        pool["id"], reason="SYMBOL_MARKET_MISMATCH"
    ) is True

    with pytest.raises(PlanBlockedError) as exc_info:
        plan_svc.generate_plan(
            account_id=account["id"], signal_date=SIGNAL_DATE,
            stock_pool_version_id=pool["id"],
            strategy_version_id=strategy["id"],
        )

    assert exc_info.value.code == "PLAN_BLOCKED"
    assert exc_info.value.details == {
        "stock_pool_version_id": pool["id"],
        "invalid_reason": "SYMBOL_MARKET_MISMATCH",
    }
    assert repo.list_plan_runs() == []


# ===================================================================
# 辅助:下跌 K 线(用于触发 EXIT)
# ===================================================================

def _gen_bars_descending(code, end_date, n, start, slope=0.03):
    """生成 n 根下跌 K 线(用于触发趋势失效退出)。"""
    bars = []
    for i in range(n):
        d = end_date - timedelta(days=n - 1 - i)
        close = round(start * ((1 - slope) ** i), 4)
        prev_close = round(start * ((1 - slope) ** (i - 1)), 4) if i > 0 else close
        bars.append(DailyBar(
            code=code, trade_date=d,
            open=prev_close,
            high=round(close * 1.01, 4),
            low=round(close * 0.97, 4),
            close=close,
            volume=1_000_000, source="test",
        ))
    return bars
