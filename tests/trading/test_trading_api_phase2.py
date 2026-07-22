import os
import pytest
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.trading.repository import TradingRepository
from backend.trading.services.account_service import AccountService
from backend.trading.services.execution_service import ExecutionService
from backend.trading.services.portfolio_service import PortfolioService
from backend.trading.migrations import run_migrations

TEST_DB = "data/test_api_phase2.db"


@pytest.fixture(autouse=True)
def setup_phase2(monkeypatch):
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    repo = TradingRepository(TEST_DB)
    import backend.trading.router as router_mod
    monkeypatch.setattr(router_mod, "trading_repo", repo)
    monkeypatch.setattr(router_mod, "account_service", AccountService(repo))
    monkeypatch.setattr(router_mod, "execution_service", ExecutionService(repo))
    monkeypatch.setattr(router_mod, "portfolio_service", PortfolioService(repo))
    yield
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


@pytest.mark.asyncio
async def test_create_account():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/trading/accounts", json={
            "name": "main", "initial_equity": 100000, "cash_balance": 100000,
        })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "main"
    assert data["risk_per_trade"] == 0.005


@pytest.mark.asyncio
async def test_list_accounts():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/trading/accounts", json={"name": "a", "initial_equity": 100000, "cash_balance": 100000})
        await ac.post("/api/trading/accounts", json={"name": "b", "initial_equity": 50000, "cash_balance": 50000})
        resp = await ac.get("/api/trading/accounts")
    assert resp.status_code == 200
    assert len(resp.json()["accounts"]) == 2


@pytest.mark.asyncio
async def test_get_account():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/api/trading/accounts", json={"name": "main", "initial_equity": 100000, "cash_balance": 100000})
        aid = r1.json()["id"]
        r2 = await ac.get(f"/api/trading/accounts/{aid}")
    assert r2.status_code == 200
    assert r2.json()["name"] == "main"


@pytest.mark.asyncio
async def test_get_account_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/trading/accounts/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_account():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/api/trading/accounts", json={"name": "main", "initial_equity": 100000, "cash_balance": 100000})
        aid = r1.json()["id"]
        r2 = await ac.put(f"/api/trading/accounts/{aid}", json={"cash_balance": 80000})
    assert r2.status_code == 200
    assert r2.json()["cash_balance"] == 80000


@pytest.mark.asyncio
async def test_update_account_initial_equity_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/api/trading/accounts", json={"name": "main", "initial_equity": 100000, "cash_balance": 100000})
        aid = r1.json()["id"]
        r2 = await ac.put(f"/api/trading/accounts/{aid}", json={"initial_equity": 999999})
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_record_buy_execution():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/api/trading/accounts", json={"name": "main", "initial_equity": 100000, "cash_balance": 100000})
        aid = r1.json()["id"]
        r2 = await ac.post("/api/trading/executions", json={
            "account_id": aid, "stock_code": "000001.SZ", "side": "BUY",
            "trade_date": "2026-07-22", "price": 10.0, "quantity": 1000,
            "commission": 5, "tax": 0, "client_execution_id": "exec-1",
        })
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert data["execution"]["side"] == "BUY"
    assert data["position"]["quantity"] == 1000


@pytest.mark.asyncio
async def test_execution_idempotent():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/api/trading/accounts", json={"name": "main", "initial_equity": 100000, "cash_balance": 100000})
        aid = r1.json()["id"]
        body = {
            "account_id": aid, "stock_code": "000001.SZ", "side": "BUY",
            "trade_date": "2026-07-22", "price": 10.0, "quantity": 1000,
            "commission": 0, "tax": 0, "client_execution_id": "dup-1",
        }
        r2 = await ac.post("/api/trading/executions", json=body)
        r3 = await ac.post("/api/trading/executions", json=body)  # 重复
    assert r2.status_code == 200
    assert r3.status_code == 200
    assert r2.json()["execution"]["id"] == r3.json()["execution"]["id"]


@pytest.mark.asyncio
async def test_get_positions():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/api/trading/accounts", json={"name": "main", "initial_equity": 100000, "cash_balance": 100000})
        aid = r1.json()["id"]
        await ac.post("/api/trading/executions", json={
            "account_id": aid, "stock_code": "000001.SZ", "side": "BUY",
            "trade_date": "2026-07-22", "price": 10.0, "quantity": 1000,
            "commission": 0, "tax": 0, "client_execution_id": "e1",
        })
        resp = await ac.get(f"/api/trading/positions?account_id={aid}")
    assert resp.status_code == 200
    assert len(resp.json()["positions"]) == 1


@pytest.mark.asyncio
async def test_list_executions():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/api/trading/accounts", json={"name": "main", "initial_equity": 100000, "cash_balance": 100000})
        aid = r1.json()["id"]
        await ac.post("/api/trading/executions", json={
            "account_id": aid, "stock_code": "000001.SZ", "side": "BUY",
            "trade_date": "2026-07-22", "price": 10.0, "quantity": 1000,
            "commission": 0, "tax": 0, "client_execution_id": "e1",
        })
        resp = await ac.get(f"/api/trading/executions?account_id={aid}&start=2026-07-22&end=2026-07-22")
    assert resp.status_code == 200
    assert len(resp.json()["executions"]) == 1


@pytest.mark.asyncio
async def test_sell_insufficient_available_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/api/trading/accounts", json={"name": "main", "initial_equity": 100000, "cash_balance": 100000})
        aid = r1.json()["id"]
        # 买入(available=0 因为 T+1)
        await ac.post("/api/trading/executions", json={
            "account_id": aid, "stock_code": "000001.SZ", "side": "BUY",
            "trade_date": "2026-07-22", "price": 10.0, "quantity": 1000,
            "commission": 0, "tax": 0, "client_execution_id": "b1",
        })
        # 当日卖出(available=0)应失败
        r2 = await ac.post("/api/trading/executions", json={
            "account_id": aid, "stock_code": "000001.SZ", "side": "SELL",
            "trade_date": "2026-07-22", "price": 12.0, "quantity": 100,
            "commission": 0, "tax": 0, "client_execution_id": "s1",
        })
    assert r2.status_code in (400, 422)
