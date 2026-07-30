"""Phase 5 API 端点测试(spec §11.2 backtests / reviews)。

覆盖:
- POST /backtests:同步运行,返回 run + 指标 + trades(200);非法输入 400。
- GET /backtests/{id}:返回状态/指标/交易;不存在 404。
- GET /reviews/summary:胜率/期望/回撤/执行率;账户不存在 400;period<=0 422。

复用 test_trading_api_phase2/phase3 的 monkeypatch + ASGITransport 模式
和上升趋势行情 fixture(使回测产生交易)。
"""
import os
from datetime import date, timedelta

import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.trading.domain import DailyBar
from backend.trading.migrations import run_migrations
from backend.trading.repository import TradingRepository
from backend.trading.services.account_service import AccountService
from backend.trading.services.backtest_service import BacktestService
from backend.trading.services.execution_service import ExecutionService
from backend.trading.services.market_data_service import MarketDataService
from backend.trading.services.plan_service import PlanService
from backend.trading.services.pool_service import PoolService
from backend.trading.services.portfolio_service import PortfolioService
from backend.trading.services.review_service import ReviewService
from backend.trading.services.strategy_service import StrategyService

TEST_DB = "data/test_api_phase5.db"
BENCHMARK = "000300.SH"


# ===================================================================
# 行情生成辅助
# ===================================================================

def _gen_bars(code, end_date, n, start_price, slope=0.004,
              hi_mult=1.02, lo_mult=0.98, volume=1_000_000):
    bars = []
    for i in range(n):
        d = end_date - timedelta(days=n - 1 - i)
        close = round(start_price * ((1 + slope) ** i), 4)
        prev_close = round(start_price * ((1 + slope) ** (i - 1)), 4) if i > 0 else close
        bars.append(DailyBar(
            code=code, trade_date=d, open=prev_close,
            high=round(close * hi_mult, 4), low=round(close * lo_mult, 4),
            close=close, volume=volume, source="test",
        ))
    return bars


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture(autouse=True)
def setup_phase5(monkeypatch):
    """临时 DB + 注入 router 单例(含 backtest/review service)。"""
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    repo = TradingRepository(TEST_DB)
    import backend.trading.router as router_mod
    mds = MarketDataService(repo, provider=None)
    pfs = PortfolioService(repo)
    monkeypatch.setattr(router_mod, "trading_repo", repo)
    monkeypatch.setattr(router_mod, "pool_service", PoolService(repo))
    monkeypatch.setattr(router_mod, "market_data_service", mds)
    monkeypatch.setattr(router_mod, "account_service", AccountService(repo))
    monkeypatch.setattr(router_mod, "execution_service", ExecutionService(repo))
    monkeypatch.setattr(router_mod, "portfolio_service", pfs)
    monkeypatch.setattr(router_mod, "strategy_service", StrategyService(repo))
    monkeypatch.setattr(router_mod, "plan_service", PlanService(repo, mds, pfs))
    monkeypatch.setattr(router_mod, "backtest_service", BacktestService(repo))
    monkeypatch.setattr(router_mod, "review_service", ReviewService(repo))
    yield repo
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


def _seed_backtest_data(repo):
    """注入策略 + 池 + 上升趋势行情,使回测产生交易。返回各 id。"""
    strat = repo.create_strategy_version(
        strategy_code="default", name="v1",
        params_json={"risk_per_trade": 0.005, "min_score": 70},
    )
    pool = repo.create_stock_pool_version(
        pool_name="default",
        items=[{"stock_code": "000001.SZ", "stock_name": "平安银行"}],
        source="text",
    )
    end = date(2026, 7, 22)
    repo.upsert_daily_bars(_gen_bars(BENCHMARK, end, 70, 3000.0, slope=0.003))
    repo.upsert_daily_bars(_gen_bars("000001.SZ", end, 70, 10.0, slope=0.004))
    return strat["id"], pool["id"]


# ===================================================================
# POST /backtests
# ===================================================================

@pytest.mark.asyncio
async def test_create_backtest_succeeds(setup_phase5):
    repo = setup_phase5
    sid, pid = _seed_backtest_data(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/trading/backtests", json={
            "strategy_version_id": sid, "stock_pool_version_id": pid,
            "start_date": "2026-06-01", "end_date": "2026-07-22",
            "initial_equity": 100000,
        })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "SUCCEEDED"
    assert data["strategy_version_id"] == sid
    assert data["stock_pool_version_id"] == pid
    assert "metrics" in data and data["metrics"] is not None
    assert "trade_count" in data["metrics"]
    assert isinstance(data["trades"], list)
    assert isinstance(data["equity_curve"], list)
    assert data["initial_equity"] == 100000
    assert "fee_params" in data


@pytest.mark.asyncio
async def test_create_backtest_with_fee_params(setup_phase5):
    repo = setup_phase5
    sid, pid = _seed_backtest_data(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/trading/backtests", json={
            "strategy_version_id": sid, "stock_pool_version_id": pid,
            "start_date": "2026-06-01", "end_date": "2026-07-22",
            "fee_params": {"commission_rate": 0.001, "min_commission": 5,
                           "stamp_tax": 0.001, "slippage": 0.0},
        })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["fee_params"]["commission_rate"] == 0.001


@pytest.mark.asyncio
async def test_create_backtest_idempotent(setup_phase5):
    """同参数重复创建返回同一 run。"""
    repo = setup_phase5
    sid, pid = _seed_backtest_data(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/api/trading/backtests", json={
            "strategy_version_id": sid, "stock_pool_version_id": pid,
            "start_date": "2026-06-01", "end_date": "2026-07-22",
        })
        r2 = await ac.post("/api/trading/backtests", json={
            "strategy_version_id": sid, "stock_pool_version_id": pid,
            "start_date": "2026-06-01", "end_date": "2026-07-22",
        })
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]


@pytest.mark.asyncio
async def test_create_backtest_nonexistent_strategy(setup_phase5):
    repo = setup_phase5
    _, pid = _seed_backtest_data(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/trading/backtests", json={
            "strategy_version_id": 99999, "stock_pool_version_id": pid,
            "start_date": "2026-06-01", "end_date": "2026-07-22",
        })
    assert resp.status_code == 400
    assert "策略版本" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_backtest_nonexistent_pool(setup_phase5):
    repo = setup_phase5
    sid, _ = _seed_backtest_data(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/trading/backtests", json={
            "strategy_version_id": sid, "stock_pool_version_id": 99999,
            "start_date": "2026-06-01", "end_date": "2026-07-22",
        })
    assert resp.status_code == 400
    assert "股票池版本" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_backtest_invalid_dates(setup_phase5):
    repo = setup_phase5
    sid, pid = _seed_backtest_data(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/trading/backtests", json={
            "strategy_version_id": sid, "stock_pool_version_id": pid,
            "start_date": "2026-07-22", "end_date": "2026-06-01",  # 反序
        })
    assert resp.status_code == 400
    assert "end_date" in resp.json()["detail"]


# ===================================================================
# GET /backtests/{id}
# ===================================================================

@pytest.mark.asyncio
async def test_get_backtest(setup_phase5):
    repo = setup_phase5
    sid, pid = _seed_backtest_data(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r_create = await ac.post("/api/trading/backtests", json={
            "strategy_version_id": sid, "stock_pool_version_id": pid,
            "start_date": "2026-06-01", "end_date": "2026-07-22",
        })
        run_id = r_create.json()["id"]
        resp = await ac.get(f"/api/trading/backtests/{run_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == run_id
    assert data["status"] == "SUCCEEDED"
    assert len(data["trades"]) == r_create.json()["metrics"]["trade_count"]


@pytest.mark.asyncio
async def test_get_backtest_not_found(setup_phase5):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/trading/backtests/99999")
    assert resp.status_code == 404


# ===================================================================
# GET /reviews/summary
# ===================================================================

def _seed_review_data(repo):
    """注入账户 + plan_run + plan_items + 成交 + 净值快照。返回 account_id。"""
    account = repo.create_account(name="main", initial_equity=100_000,
                                  cash_balance=100_000)
    created = repo.create_plan_run(
        run_key="rk1", account_id=account["id"], signal_date=date.today().isoformat(),
        target_trade_date=date.today().isoformat(), stock_pool_version_id=1,
        strategy_version_id=1, status="PUBLISHED",
        account_snapshot_json={}, data_snapshot_hash="h",
    )
    item = repo.create_plan_item(
        plan_run_id=created["id"], stock_code="000001.SZ",
        action="CONDITIONAL_BUY", rank_no=1, stop_price=9.98,
        suggested_quantity=1000,
    )
    today = date.today()
    d1 = (today - timedelta(days=5)).isoformat()
    d2 = (today - timedelta(days=2)).isoformat()
    repo.create_execution(
        account_id=account["id"], stock_code="000001.SZ", side="BUY",
        trade_date=d1, price=10.0, quantity=1000,
        client_execution_id="ce1", plan_item_id=item["id"],
    )
    repo.create_execution(
        account_id=account["id"], stock_code="000001.SZ", side="SELL",
        trade_date=d2, price=11.0, quantity=1000,
        client_execution_id="ce2", plan_item_id=item["id"],
    )
    repo.upsert_equity_snapshot(
        account_id=account["id"], trade_date=d1, cash=90000, market_value=10000,
        total_equity=100000, exposure=0.1, peak_equity=100000, drawdown=0.0,
    )
    repo.upsert_equity_snapshot(
        account_id=account["id"], trade_date=d2, cash=101000, market_value=0,
        total_equity=101000, exposure=0.0, peak_equity=101000, drawdown=0.0,
    )
    return account["id"]


@pytest.mark.asyncio
async def test_review_summary(setup_phase5):
    repo = setup_phase5
    aid = _seed_review_data(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/trading/reviews/summary?account_id={aid}&period=30")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["account_id"] == aid
    assert data["trade_count"] == 1
    assert data["win_rate"] == 1.0
    assert data["execution_rate"] == 1.0
    for key in ("expectancy", "max_drawdown", "profit_factor", "avg_win_r"):
        assert key in data


@pytest.mark.asyncio
async def test_review_summary_default_period(setup_phase5):
    repo = setup_phase5
    aid = _seed_review_data(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/trading/reviews/summary?account_id={aid}")
    assert resp.status_code == 200
    assert resp.json()["period"] == 30


@pytest.mark.asyncio
async def test_review_summary_account_not_found(setup_phase5):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/trading/reviews/summary?account_id=99999")
    assert resp.status_code == 400
    assert "账户" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_review_summary_invalid_period(setup_phase5):
    """period <= 0 → Query gt=0 → 422。"""
    repo = setup_phase5
    aid = _seed_review_data(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/trading/reviews/summary?account_id={aid}&period=0")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_review_summary_empty_account(setup_phase5):
    """无成交的账户 → 200 + 全 0 指标。"""
    repo = setup_phase5
    acc = repo.create_account(name="empty", initial_equity=10000, cash_balance=10000)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/trading/reviews/summary?account_id={acc['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["trade_count"] == 0
    assert data["win_rate"] == 0.0
