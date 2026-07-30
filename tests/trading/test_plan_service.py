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
from datetime import date, timedelta

import pytest

from backend.trading.domain import DailyBar
from backend.trading.errors import (
    PlanAlreadyPublishedError,
    PlanBlockedError,
    StrategyNotActiveError,
)
from backend.trading.migrations import run_migrations
from backend.trading.repository import TradingRepository
from backend.trading.services.market_data_service import MarketDataService
from backend.trading.services.plan_service import PlanService
from backend.trading.services.portfolio_service import PortfolioService
from backend.trading.services.strategy_service import StrategyService

TEST_DB = "data/test_plan_svc.db"
SIGNAL_DATE = date(2026, 7, 22)        # 周三
TARGET_TRADE_DATE = date(2026, 7, 23)  # 周四
BENCHMARK = "000300.SH"


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
    plan_svc = PlanService(repo, md_svc, pf_svc)

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
    repo.upsert_daily_bars(_gen_bars(BENCHMARK, end_date, n, start, slope=0.003))


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


def test_blocked_run_can_regenerate(setup):
    """BLOCKED 状态的 run 允许重新生成(spec:BLOCKED/FAILED 可重建)。"""
    repo, md_svc, pf_svc, plan_svc, account, strategy, pool = setup
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)
    # 持仓缺行情 → BLOCKED
    repo.upsert_position(
        account_id=account["id"], stock_code="600000.SH",
        quantity=1000, available_quantity=1000, average_cost=10.0,
    )
    with pytest.raises(PlanBlockedError):
        plan_svc.generate_plan(
            account_id=account["id"], signal_date=SIGNAL_DATE,
            stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
        )
    # 补上持仓行情后,同 run_key 应能重新生成(旧 BLOCKED 不阻止)
    repo.upsert_daily_bars(_gen_bars("600000.SH", SIGNAL_DATE, 65, 10.0))
    r2 = plan_svc.generate_plan(
        account_id=account["id"], signal_date=SIGNAL_DATE,
        stock_pool_version_id=pool["id"], strategy_version_id=strategy["id"],
    )
    # 同 inputs → 同 run_key → 旧 BLOCKED 被覆盖重建为 READY/PARTIAL
    assert r2["status"] in ("READY", "PARTIAL")
    assert r2["reused"] is False


# ===================================================================
# 状态机:CREATED→VALIDATING→GENERATING→READY 可观测
# ===================================================================

class _StatusCapturingRepo:
    """包装 repo,记录所有 update_plan_run_status 调用序列。"""

    def __init__(self, inner):
        self._inner = inner
        self.transitions: list[tuple[int, str]] = []

    def update_plan_run_status(self, run_id, status, **kwargs):
        self.transitions.append((run_id, status))
        return self._inner.update_plan_run_status(run_id, status, **kwargs)

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
