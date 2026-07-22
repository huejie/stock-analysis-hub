import os
from datetime import date
import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.trading.repository import TradingRepository
from backend.trading.services.pool_service import PoolService
from backend.trading.services.market_data_service import MarketDataService
from backend.trading.migrations import run_migrations

TEST_DB = "data/test_trading_api.db"


@pytest.fixture(autouse=True)
def setup_trading(monkeypatch):
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    repo = TradingRepository(TEST_DB)

    # 替换 trading 模块共享的 repo/services（模块级单例）
    import backend.trading.router as router_mod
    monkeypatch.setattr(router_mod, "trading_repo", repo)
    monkeypatch.setattr(router_mod, "pool_service", PoolService(repo))
    monkeypatch.setattr(router_mod, "market_data_service", MarketDataService(repo, provider=None))
    yield
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


@pytest.mark.asyncio
async def test_import_stock_pool_text():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/trading/stock-pools/import", json={
            "pool_name": "default",
            "source": "text",
            "text_body": "000001 平安银行\n600000 浦发银行",
        })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["version_no"] == 1
    assert data["items_count"] == 2


@pytest.mark.asyncio
async def test_import_stock_pool_csv():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/trading/stock-pools/import", json={
            "pool_name": "default",
            "source": "csv",
            "text_body": "code,name\n000001,平安\n600000,浦发",
        })
    assert resp.status_code == 200, resp.text
    assert resp.json()["items_count"] == 2


@pytest.mark.asyncio
async def test_import_empty_pool_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/trading/stock-pools/import", json={
            "pool_name": "default",
            "source": "text",
            "text_body": "no valid codes here",
        })
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_get_stock_pool_version():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/api/trading/stock-pools/import", json={
            "pool_name": "default", "source": "text",
            "text_body": "000001 平安",
        })
        vid = r1.json()["id"]
        r2 = await ac.get(f"/api/trading/stock-pools/{vid}")
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert data["version_no"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["stock_name"] == "平安"


@pytest.mark.asyncio
async def test_list_stock_pools():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/trading/stock-pools/import", json={
            "pool_name": "default", "source": "text", "text_body": "000001"})
        await ac.post("/api/trading/stock-pools/import", json={
            "pool_name": "default", "source": "text", "text_body": "000001\n600000"})
        resp = await ac.get("/api/trading/stock-pools")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["versions"]) == 2


@pytest.mark.asyncio
async def test_data_health_blocked_when_no_data():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/trading/stock-pools/import", json={
            "pool_name": "default", "source": "text", "text_body": "000001"})
        resp = await ac.get("/api/trading/data-health?trade_date=2026-07-20")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 基准未更新 -> BLOCKED
    assert data["overall_status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_create_data_job():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/trading/data-jobs", json={
            "job_type": "update_bars", "trade_date": "2026-07-20",
        })
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "QUEUED"


@pytest.mark.asyncio
async def test_get_data_job():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/api/trading/data-jobs", json={
            "job_type": "update_bars", "trade_date": "2026-07-20"})
        jid = r1.json()["job_id"]
        r2 = await ac.get(f"/api/trading/data-jobs/{jid}")
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == jid


@pytest.mark.asyncio
async def test_get_data_job_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/trading/data-jobs/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_stock_pool_version_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/trading/stock-pools/99999")
    assert resp.status_code == 404
