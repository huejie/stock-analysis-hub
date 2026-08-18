"""Phase 3 API 端点测试(spec §11.3/11.4)。

覆盖 /strategies 与 /plan-runs 端点的请求/响应形状与错误码。
复用 test_trading_api_phase2 的 monkeypatch + ASGITransport 模式,
以及 test_plan_service 的行情生成辅助(基准 + 池股票 60+/20+ K 线)。
"""
import os
from datetime import date, timedelta

import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.trading.domain import DailyBar
from backend.trading.errors import (
    PlanGenerationFailedError,
    PlanSupersededError,
)
from backend.trading.migrations import run_migrations
from backend.trading.repository import TradingRepository
from backend.trading.services.account_service import AccountService
from backend.trading.services.execution_service import ExecutionService
from backend.trading.services.market_data_service import MarketDataService
from backend.trading.services.plan_service import PlanService
from backend.trading.services.pool_service import PoolService
from backend.trading.services.portfolio_service import PortfolioService
from backend.trading.services.strategy_service import StrategyService

TEST_DB = "data/test_api_phase3.db"
SIGNAL_DATE = date(2026, 7, 22)        # 周三
TARGET_TRADE_DATE = date(2026, 7, 23)  # 周四
BENCHMARKS = ["000300.SH", "000905.SH"]
BENCHMARK = BENCHMARKS[0]


# ===================================================================
# 行情生成辅助(升序、防未来函数、使 regime=ATTACK 且评分>=70)
# ===================================================================

def _gen_bars(code, end_date, n, start_price, slope=0.004,
              hi_mult=1.02, lo_mult=0.98, volume=1_000_000):
    """生成 n 根升序 K 线(日期 <= end_date,防未来函数)。"""
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
# Fixtures
# ===================================================================

@pytest.fixture(autouse=True)
def setup_phase3(monkeypatch):
    """临时 DB + 注入 router 单例(同 phase2 模式)。"""
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    repo = TradingRepository(TEST_DB)
    import backend.trading.router as router_mod
    monkeypatch.setattr(router_mod, "trading_repo", repo)
    monkeypatch.setattr(router_mod, "pool_service", PoolService(repo))
    monkeypatch.setattr(router_mod, "market_data_service", MarketDataService(repo, provider=None))
    monkeypatch.setattr(router_mod, "account_service", AccountService(repo))
    monkeypatch.setattr(router_mod, "execution_service", ExecutionService(repo))
    monkeypatch.setattr(router_mod, "portfolio_service", PortfolioService(repo))
    monkeypatch.setattr(router_mod, "strategy_service", StrategyService(repo))
    plan_service = PlanService(
        repo, MarketDataService(repo, provider=None), PortfolioService(repo),
        benchmark_codes=BENCHMARKS,
    )
    monkeypatch.setattr(router_mod, "plan_service", plan_service)
    yield
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


@pytest.fixture
def seeded_setup():
    """准备 account + 激活策略 + 股票池 + 基准/池行情,返回各 id。

    直接通过 repo/service 写入(避免 async fixture 复杂度),行情需要 60+/20+ K 线。
    """
    repo = TradingRepository(TEST_DB)
    # 账户
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
    # 股票池
    pool = repo.create_stock_pool_version(
        pool_name="default",
        items=[
            {"stock_code": "000001.SZ", "stock_name": "平安银行"},
            {"stock_code": "600519.SH", "stock_name": "贵州茅台"},
        ],
        source="text",
    )
    # 行情(基准 70 根 + 池股票 65 根,满足指标计算窗口)
    _add_benchmark(repo)
    _add_pool_stock(repo, "000001.SZ")
    _add_pool_stock(repo, "600519.SH", start=1600.0)

    return {
        "account_id": account["id"],
        "strategy_version_id": created["id"],
        "pool_version_id": pool["id"],
    }


# ===================================================================
# 策略端点
# ===================================================================

@pytest.mark.asyncio
async def test_create_strategy():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/trading/strategies", json={
            "strategy_code": "default", "name": "v1",
            "params_json": {"risk_per_trade": 0.005, "min_score": 70},
        })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["strategy_code"] == "default"
    assert data["status"] == "DRAFT"
    assert data["version_no"] >= 1
    assert data["params_hash"]
    assert data["id"] >= 1


@pytest.mark.asyncio
async def test_list_strategies():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/trading/strategies", json={
            "strategy_code": "default", "name": "v1",
            "params_json": {"risk_per_trade": 0.005},
        })
        await ac.post("/api/trading/strategies", json={
            "strategy_code": "momentum", "name": "v1",
            "params_json": {"risk_per_trade": 0.01},
        })
        resp_all = await ac.get("/api/trading/strategies")
        resp_filtered = await ac.get("/api/trading/strategies?strategy_code=default")
    assert resp_all.status_code == 200
    assert len(resp_all.json()["strategies"]) == 2
    assert resp_filtered.status_code == 200
    assert len(resp_filtered.json()["strategies"]) == 1
    assert resp_filtered.json()["strategies"][0]["strategy_code"] == "default"


@pytest.mark.asyncio
async def test_activate_strategy():
    """门禁通过(有合格回测)→ 200 ACTIVE,无 warning。"""
    import backend.trading.router as router_mod
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r_create = await ac.post("/api/trading/strategies", json={
            "strategy_code": "default", "name": "v1",
            "params_json": {"risk_per_trade": 0.005},
        })
        sid = r_create.json()["id"]
        _seed_passing_backtest(router_mod.trading_repo, sid)
        resp = await ac.post(f"/api/trading/strategies/{sid}/activate")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == sid
    assert data["status"] == "ACTIVE"
    assert isinstance(data["warnings"], list)
    assert data["warnings"] == []  # 门禁通过,无 warning(Phase 5 真校验)


@pytest.mark.asyncio
async def test_activate_strategy_no_backtest_422():
    """无回测数据 → 激活被拒(400)。spec §14.4。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r_create = await ac.post("/api/trading/strategies", json={
            "strategy_code": "default", "name": "v1",
            "params_json": {"risk_per_trade": 0.005},
        })
        sid = r_create.json()["id"]
        resp = await ac.post(f"/api/trading/strategies/{sid}/activate")
    assert resp.status_code == 400
    assert "无回测数据" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_activate_strategy_retires_previous():
    """激活新版本时旧 ACTIVE 自动 RETIRED(两条都有合格回测)。"""
    import backend.trading.router as router_mod
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/api/trading/strategies", json={
            "strategy_code": "default", "name": "v1",
            "params_json": {"risk_per_trade": 0.005},
        })
        sid1 = r1.json()["id"]
        _seed_passing_backtest(router_mod.trading_repo, sid1)
        await ac.post(f"/api/trading/strategies/{sid1}/activate")
        r2 = await ac.post("/api/trading/strategies", json={
            "strategy_code": "default", "name": "v2",
            "params_json": {"risk_per_trade": 0.006},  # 不同 params → 新 hash
        })
        sid2 = r2.json()["id"]
        _seed_passing_backtest(router_mod.trading_repo, sid2)
        resp = await ac.post(f"/api/trading/strategies/{sid2}/activate")
    assert resp.status_code == 200
    assert resp.json()["retired_previous_id"] == sid1


@pytest.mark.asyncio
async def test_activate_strategy_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/trading/strategies/99999/activate")
    assert resp.status_code == 404


# ===================================================================
# 计划端点
# ===================================================================

@pytest.mark.asyncio
async def test_create_plan_run(seeded_setup):
    """正常生成 → 200,run_key/status/signal_date/target_trade_date 齐。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/trading/plan-runs", json={
            "account_id": seeded_setup["account_id"],
            "signal_date": SIGNAL_DATE.isoformat(),
            "stock_pool_version_id": seeded_setup["pool_version_id"],
            "strategy_version_id": seeded_setup["strategy_version_id"],
        })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["run_key"]
    assert len(data["run_key"]) >= 16
    assert data["status"] in ("READY", "PARTIAL")
    assert data["signal_date"] == SIGNAL_DATE.isoformat()
    assert data["target_trade_date"] == TARGET_TRADE_DATE.isoformat()
    assert data["reused"] is False
    assert data["id"] >= 1


@pytest.mark.asyncio
async def test_create_plan_run_idempotent(seeded_setup):
    """相同输入二次生成 → 同 id,reused=True。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        body = {
            "account_id": seeded_setup["account_id"],
            "signal_date": SIGNAL_DATE.isoformat(),
            "stock_pool_version_id": seeded_setup["pool_version_id"],
            "strategy_version_id": seeded_setup["strategy_version_id"],
        }
        r1 = await ac.post("/api/trading/plan-runs", json=body)
        r2 = await ac.post("/api/trading/plan-runs", json=body)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.json()["reused"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["CREATED", "VALIDATING", "GENERATING"])
async def test_create_plan_run_reused_in_progress_returns_202(
    seeded_setup, monkeypatch, status
):
    import backend.trading.router as router_mod

    class InProgressPlanService:
        def generate_plan(self, **_kwargs):
            return {
                "id": 77,
                "run_key": "in-progress-key",
                "status": status,
                "signal_date": SIGNAL_DATE.isoformat(),
                "target_trade_date": TARGET_TRADE_DATE.isoformat(),
                "reused": True,
            }

    monkeypatch.setattr(router_mod, "plan_service", InProgressPlanService())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/trading/plan-runs", json={
            "account_id": seeded_setup["account_id"],
            "signal_date": SIGNAL_DATE.isoformat(),
            "stock_pool_version_id": seeded_setup["pool_version_id"],
            "strategy_version_id": seeded_setup["strategy_version_id"],
        })

    assert response.status_code == 202
    assert response.json() == {
        "id": 77,
        "run_key": "in-progress-key",
        "status": status,
        "signal_date": SIGNAL_DATE.isoformat(),
        "target_trade_date": TARGET_TRADE_DATE.isoformat(),
        "reused": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (
            PlanGenerationFailedError(
                "persisted failure", details={"run_id": 71}
            ),
            500,
            "PLAN_GENERATION_FAILED",
        ),
        (
            PlanSupersededError(
                "persisted superseded", details={"run_id": 72}
            ),
            409,
            "PLAN_SUPERSEDED",
        ),
    ],
)
async def test_create_plan_run_terminal_errors_are_structured(
    seeded_setup, monkeypatch, error, status_code, code
):
    import backend.trading.router as router_mod

    class ErrorPlanService:
        def generate_plan(self, **_kwargs):
            raise error

    monkeypatch.setattr(router_mod, "plan_service", ErrorPlanService())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/trading/plan-runs", json={
            "account_id": seeded_setup["account_id"],
            "signal_date": SIGNAL_DATE.isoformat(),
            "stock_pool_version_id": seeded_setup["pool_version_id"],
            "strategy_version_id": seeded_setup["strategy_version_id"],
        })

    assert response.status_code == status_code
    assert response.json()["detail"] == {
        "code": code,
        "message": error.message,
        "details": error.details,
    }


@pytest.mark.asyncio
async def test_list_plan_runs(seeded_setup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/trading/plan-runs", json={
            "account_id": seeded_setup["account_id"],
            "signal_date": SIGNAL_DATE.isoformat(),
            "stock_pool_version_id": seeded_setup["pool_version_id"],
            "strategy_version_id": seeded_setup["strategy_version_id"],
        })
        resp_all = await ac.get("/api/trading/plan-runs")
        resp_by_date = await ac.get(
            f"/api/trading/plan-runs?signal_date={SIGNAL_DATE.isoformat()}"
        )
        resp_other = await ac.get("/api/trading/plan-runs?signal_date=2026-01-01")
    assert resp_all.status_code == 200
    assert len(resp_all.json()["plan_runs"]) >= 1
    assert resp_by_date.status_code == 200
    assert len(resp_by_date.json()["plan_runs"]) >= 1
    assert resp_other.status_code == 200
    assert resp_other.json()["plan_runs"] == []


@pytest.mark.asyncio
async def test_list_plan_runs_filters_two_accounts_and_returns_account_id(
    seeded_setup,
):
    import backend.trading.router as router_mod

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/trading/plan-runs",
            json={
                "account_id": seeded_setup["account_id"],
                "signal_date": SIGNAL_DATE.isoformat(),
                "stock_pool_version_id": seeded_setup["pool_version_id"],
                "strategy_version_id": seeded_setup["strategy_version_id"],
            },
        )
        other_account = router_mod.trading_repo.create_account(
            name="secondary",
            initial_equity=50_000,
            cash_balance=50_000,
            is_active=False,
        )
        other_run = router_mod.trading_repo.create_plan_run(
            run_key="api-other-account",
            account_id=other_account["id"],
            signal_date=SIGNAL_DATE.isoformat(),
            target_trade_date=TARGET_TRADE_DATE.isoformat(),
            stock_pool_version_id=seeded_setup["pool_version_id"],
            strategy_version_id=seeded_setup["strategy_version_id"],
            status="READY",
            account_snapshot_json={"cash_balance": 50_000},
            data_snapshot_hash="api-other-snapshot",
        )

        first_response = await client.get(
            "/api/trading/plan-runs",
            params={"account_id": seeded_setup["account_id"]},
        )
        other_response = await client.get(
            "/api/trading/plan-runs",
            params={"account_id": other_account["id"]},
        )

    assert created.status_code == 200
    assert first_response.status_code == 200
    assert len(first_response.json()["plan_runs"]) == 1
    assert first_response.json()["plan_runs"][0]["id"] == created.json()["id"]
    assert (
        first_response.json()["plan_runs"][0]["account_id"]
        == seeded_setup["account_id"]
    )
    assert [row["id"] for row in other_response.json()["plan_runs"]] == [
        other_run["id"]
    ]
    assert other_response.json()["plan_runs"][0]["account_id"] == other_account["id"]


@pytest.mark.asyncio
async def test_get_plan_detail(seeded_setup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r_run = await ac.post("/api/trading/plan-runs", json={
            "account_id": seeded_setup["account_id"],
            "signal_date": SIGNAL_DATE.isoformat(),
            "stock_pool_version_id": seeded_setup["pool_version_id"],
            "strategy_version_id": seeded_setup["strategy_version_id"],
        })
        run_id = r_run.json()["id"]
        resp = await ac.get(f"/api/trading/plan-runs/{run_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for key in ("id", "status", "signal_date", "target_trade_date",
                "market_regime", "market_score", "recommended_exposure",
                "warnings", "items", "created_at"):
        assert key in data, f"missing key {key}"
    assert data["market_regime"] == "ATTACK"
    assert data["degraded"] is True
    assert "市场宽度数据不可用" in data["warnings"][0]
    assert isinstance(data["items"], list)
    assert len(data["items"]) >= 1
    # items 形状: rule_hits/rule_misses 为 list(spec §11.4)
    it = data["items"][0]
    assert "rule_hits" in it
    assert "rule_misses" in it
    assert isinstance(it["rule_hits"], list)


@pytest.mark.asyncio
async def test_health_and_plan_block_when_configured_benchmark_missing(seeded_setup):
    """基准在复用前变为缺失时,数据健康与计划生成必须同时 BLOCKED。"""
    import backend.trading.router as router_mod

    body = {
        "account_id": seeded_setup["account_id"],
        "signal_date": SIGNAL_DATE.isoformat(),
        "stock_pool_version_id": seeded_setup["pool_version_id"],
        "strategy_version_id": seeded_setup["strategy_version_id"],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        initial_plan = await ac.post("/api/trading/plan-runs", json=body)
    assert initial_plan.status_code == 200

    conn = router_mod.trading_repo._conn()
    try:
        conn.execute(
            "DELETE FROM trade_daily_bars WHERE stock_code = ?",
            ("000905.SH",),
        )
        conn.commit()
    finally:
        conn.close()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        health = await ac.get(
            f"/api/trading/data-health?trade_date={SIGNAL_DATE.isoformat()}"
        )
        plan = await ac.post("/api/trading/plan-runs", json=body)

    assert health.status_code == 200
    health_data = health.json()
    assert health_data["benchmark_codes"] == BENCHMARKS
    assert health_data["benchmark_updated"]["000905.SH"] is False
    assert health_data["overall_status"] == "BLOCKED"
    assert plan.status_code == 409
    assert (
        plan.json()["detail"]["details"]["health"]["benchmark_updated"]
        == health_data["benchmark_updated"]
    )


@pytest.mark.asyncio
async def test_fresh_plan_blocks_when_configured_benchmark_missing(seeded_setup):
    """首次计划请求前缺 000905.SH 时,health 与 fresh plan 同时 BLOCKED。"""
    import backend.trading.router as router_mod

    conn = router_mod.trading_repo._conn()
    try:
        conn.execute(
            "DELETE FROM trade_daily_bars WHERE stock_code = ?",
            ("000905.SH",),
        )
        conn.commit()
    finally:
        conn.close()

    body = {
        "account_id": seeded_setup["account_id"],
        "signal_date": SIGNAL_DATE.isoformat(),
        "stock_pool_version_id": seeded_setup["pool_version_id"],
        "strategy_version_id": seeded_setup["strategy_version_id"],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        health = await ac.get(
            f"/api/trading/data-health?trade_date={SIGNAL_DATE.isoformat()}"
        )
        plan = await ac.post("/api/trading/plan-runs", json=body)
        replayed_plan = await ac.post("/api/trading/plan-runs", json=body)

    assert health.status_code == 200
    health_data = health.json()
    assert health_data["benchmark_codes"] == BENCHMARKS
    assert health_data["benchmark_updated"]["000905.SH"] is False
    assert health_data["overall_status"] == "BLOCKED"
    assert plan.status_code == 409
    assert replayed_plan.status_code == 409
    assert replayed_plan.json() == plan.json()
    assert (
        plan.json()["detail"]["details"]["health"]["benchmark_updated"]
        == health_data["benchmark_updated"]
    )


@pytest.mark.asyncio
async def test_get_plan_detail_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/trading/plan-runs/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_publish_plan(seeded_setup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r_run = await ac.post("/api/trading/plan-runs", json={
            "account_id": seeded_setup["account_id"],
            "signal_date": SIGNAL_DATE.isoformat(),
            "stock_pool_version_id": seeded_setup["pool_version_id"],
            "strategy_version_id": seeded_setup["strategy_version_id"],
        })
        run_id = r_run.json()["id"]
        resp = await ac.post(f"/api/trading/plan-runs/{run_id}/publish")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == run_id
    assert data["status"] == "PUBLISHED"
    assert data["published_at"]


@pytest.mark.asyncio
async def test_publish_already_published(seeded_setup):
    """重复发布 → 409 PLAN_ALREADY_PUBLISHED。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r_run = await ac.post("/api/trading/plan-runs", json={
            "account_id": seeded_setup["account_id"],
            "signal_date": SIGNAL_DATE.isoformat(),
            "stock_pool_version_id": seeded_setup["pool_version_id"],
            "strategy_version_id": seeded_setup["strategy_version_id"],
        })
        run_id = r_run.json()["id"]
        first = await ac.post(f"/api/trading/plan-runs/{run_id}/publish")
        second = await ac.post(f"/api/trading/plan-runs/{run_id}/publish")
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "PLAN_ALREADY_PUBLISHED"


@pytest.mark.asyncio
async def test_create_plan_strategy_not_active(seeded_setup):
    """DRAFT 策略(未激活)生成计划 → 422 STRATEGY_NOT_ACTIVE。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 新建一个 DRAFT 策略(不激活)
        r_draft = await ac.post("/api/trading/strategies", json={
            "strategy_code": "default", "name": "v2-draft",
            "params_json": {"risk_per_trade": 0.005, "min_score": 75},  # 不同 params
        })
        draft_id = r_draft.json()["id"]
        resp = await ac.post("/api/trading/plan-runs", json={
            "account_id": seeded_setup["account_id"],
            "signal_date": SIGNAL_DATE.isoformat(),
            "stock_pool_version_id": seeded_setup["pool_version_id"],
            "strategy_version_id": draft_id,
        })
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "STRATEGY_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_create_plan_account_not_found(seeded_setup):
    """不存在的账户 → 400。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/trading/plan-runs", json={
            "account_id": 99999,
            "signal_date": SIGNAL_DATE.isoformat(),
            "stock_pool_version_id": seeded_setup["pool_version_id"],
            "strategy_version_id": seeded_setup["strategy_version_id"],
        })
    assert resp.status_code == 400
