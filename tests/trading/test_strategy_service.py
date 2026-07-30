"""StrategyService 测试(Phase 5:激活门禁 §14.4 真校验)。

覆盖:
- 创建策略:返回 DRAFT + params_hash 幂等。
- 激活门禁:无回测 → 报错;交易数/期望/PF/回撤/集中度门槛逐项校验。
- 激活成功:DRAFT→ACTIVE,旧 ACTIVE→RETIRED,无 stub warning。
- 已 RETIRED 不能再激活;不存在报错。
- list / get_active。
"""
import os
import pytest
from backend.trading.repository import TradingRepository
from backend.trading.migrations import run_migrations
from backend.trading.services.strategy_service import StrategyService

TEST_DB = "data/test_strategy_service.db"


@pytest.fixture
def repo():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    r = TradingRepository(TEST_DB)
    yield r
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


@pytest.fixture
def service(repo):
    return StrategyService(repo)


def _job_id(n):
    return 1000 + n


def _seed_backtest(repo, version_id, *, trade_count=100, expectancy=0.5,
                   profit_factor=1.5, max_drawdown=0.1,
                   concentrated_stock=False, concentrated_month=False,
                   max_stock_share=0.3, max_month_share=0.3,
                   status="SUCCEEDED"):
    """注入一条 SUCCEEDED 回测(含 metrics),用于激活门禁。"""
    created = repo.create_backtest_run(
        job_id=_job_id(version_id) + int(status == "SUCCEEDED"),
        strategy_version_id=version_id, stock_pool_version_id=1,
        start_date="2026-01-01", end_date="2026-06-30",
        initial_equity=100000, fee_params_json={"commission_rate": 0.0003},
        status="RUNNING",
    )
    repo.update_backtest_run(
        created["id"], status=status,
        metrics_json={
            "trade_count": trade_count, "expectancy": expectancy,
            "profit_factor": profit_factor, "max_drawdown": max_drawdown,
            "concentration": {
                "max_stock_share": max_stock_share,
                "max_month_share": max_month_share,
                "concentrated_stock": concentrated_stock,
                "concentrated_month": concentrated_month,
            },
        },
    )
    return created["id"]


# ===================================================================
# 创建
# ===================================================================

def test_create_strategy_returns_draft(service):
    out = service.create_strategy(
        strategy_code="default", name="v1",
        params_json={"risk_per_trade": 0.005, "min_score": 70},
    )
    assert out["status"] == "DRAFT"
    assert out["version_no"] == 1
    assert out["reused"] is False


def test_create_strategy_idempotent_on_params(service):
    p1 = {"risk_per_trade": 0.005, "min_score": 70}
    p2 = {"min_score": 70, "risk_per_trade": 0.005}  # 键顺序不同,同 hash
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json=p1)
    v2 = service.create_strategy(strategy_code="default", name="v1-dup", params_json=p2)
    assert v1["id"] == v2["id"]
    assert v2["reused"] is True


# ===================================================================
# 激活门禁:无回测
# ===================================================================

def test_activate_no_backtest_raises(service, repo):
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    with pytest.raises(ValueError, match="无回测数据"):
        service.activate_strategy(v1["id"])


def test_activate_failed_backtest_only_treated_as_no_backtest(service, repo):
    """只有 FAILED 回测 → 视同无可用回测数据。"""
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    _seed_backtest(repo, v1["id"], status="FAILED")
    with pytest.raises(ValueError, match="无回测数据"):
        service.activate_strategy(v1["id"])


# ===================================================================
# 激活门禁:逐项失败
# ===================================================================

def test_activate_gate_fails_low_trade_count(service, repo):
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    _seed_backtest(repo, v1["id"], trade_count=50)
    with pytest.raises(ValueError, match="交易笔数"):
        service.activate_strategy(v1["id"])


def test_activate_gate_fails_zero_expectancy(service, repo):
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    _seed_backtest(repo, v1["id"], trade_count=100, expectancy=0.0)
    with pytest.raises(ValueError, match="期望值"):
        service.activate_strategy(v1["id"])


def test_activate_gate_fails_low_profit_factor(service, repo):
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    _seed_backtest(repo, v1["id"], trade_count=100, expectancy=0.5, profit_factor=0.8)
    with pytest.raises(ValueError, match="盈利因子"):
        service.activate_strategy(v1["id"])


def test_activate_gate_fails_high_drawdown(service, repo):
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    _seed_backtest(repo, v1["id"], trade_count=100, expectancy=0.5,
                   profit_factor=1.5, max_drawdown=0.5)
    with pytest.raises(ValueError, match="最大回撤"):
        service.activate_strategy(v1["id"])


def test_activate_gate_fails_concentration(service, repo):
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    _seed_backtest(repo, v1["id"], trade_count=100, expectancy=0.5,
                   profit_factor=1.5, max_drawdown=0.1,
                   concentrated_stock=True, max_stock_share=0.7)
    with pytest.raises(ValueError, match="单一股票"):
        service.activate_strategy(v1["id"])


def test_activate_gate_lists_multiple_failures(service, repo):
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    _seed_backtest(repo, v1["id"], trade_count=10, expectancy=-0.1,
                   profit_factor=0.5, max_drawdown=0.6,
                   concentrated_stock=True, concentrated_month=True)
    with pytest.raises(ValueError) as ei:
        service.activate_strategy(v1["id"])
    msg = str(ei.value)
    # 多个失败门都列出
    assert "交易笔数" in msg
    assert "期望值" in msg
    assert "盈利因子" in msg
    assert "最大回撤" in msg
    assert "单一股票" in msg


# ===================================================================
# 激活成功 + 退役旧版本
# ===================================================================

def test_activate_success_activates_and_retires_previous(service, repo):
    """门禁通过:DRAFT→ACTIVE,旧 ACTIVE→RETIRED,无 stub warning。"""
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    v2 = service.create_strategy(strategy_code="default", name="v2", params_json={"a": 2})
    _seed_backtest(repo, v1["id"])
    _seed_backtest(repo, v2["id"])

    r1 = service.activate_strategy(v1["id"])
    assert r1["status"] == "ACTIVE"
    assert r1["retired_previous_id"] is None
    # 门禁通过 → 无 warning(stub 时代结束)
    assert r1["warnings"] == []

    r2 = service.activate_strategy(v2["id"])
    assert r2["retired_previous_id"] == v1["id"]
    assert service.get_strategy(v1["id"])["status"] == "RETIRED"
    assert service.get_strategy(v2["id"])["status"] == "ACTIVE"


def test_activate_profit_factor_none_inf_passes(service, repo):
    """profit_factor=None(inf,全胜)视为 >1,门禁通过。"""
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    _seed_backtest(repo, v1["id"], profit_factor=None)
    r = service.activate_strategy(v1["id"])
    assert r["status"] == "ACTIVE"


def test_activate_uses_latest_backtest(service, repo):
    """门禁取最新一次 SUCCEEDED 回测(较早的不合格但最新合格则通过)。"""
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    # 第一次回测不合格(交易数不足)
    bad = repo.create_backtest_run(
        job_id=9001, strategy_version_id=v1["id"], stock_pool_version_id=1,
        start_date="2026-01-01", end_date="2026-03-31",
        initial_equity=100000, fee_params_json={"commission_rate": 0.0003},
        status="RUNNING",
    )
    repo.update_backtest_run(
        bad["id"], status="SUCCEEDED",
        metrics_json={"trade_count": 10, "expectancy": 0.5, "profit_factor": 1.5,
                      "max_drawdown": 0.1, "concentration": {}},
    )
    # 第二次(更新)合格
    _seed_backtest(repo, v1["id"])
    r = service.activate_strategy(v1["id"])
    assert r["status"] == "ACTIVE"


# ===================================================================
# 查询 / 边界
# ===================================================================

def test_get_active_strategy(service, repo):
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    _seed_backtest(repo, v1["id"])
    service.activate_strategy(v1["id"])
    active = service.get_active_strategy("default")
    assert active is not None
    assert active["id"] == v1["id"]


def test_get_active_strategy_none(service):
    service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    assert service.get_active_strategy("default") is None


def test_list_strategies(service):
    service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    service.create_strategy(strategy_code="default", name="v2", params_json={"a": 2})
    assert len(service.list_strategies("default")) == 2
    assert len(service.list_strategies()) == 2


def test_activate_strategy_nonexistent_raises(service):
    with pytest.raises(ValueError, match="不存在"):
        service.activate_strategy(99999)


def test_activate_strategy_retired_raises(service, repo):
    """已 RETIRED 的策略不能再激活(即使有合格回测)。"""
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    v2 = service.create_strategy(strategy_code="default", name="v2", params_json={"a": 2})
    _seed_backtest(repo, v1["id"])
    _seed_backtest(repo, v2["id"])
    service.activate_strategy(v1["id"])
    service.activate_strategy(v2["id"])  # v1 → RETIRED
    with pytest.raises(ValueError, match="退役"):
        service.activate_strategy(v1["id"])
