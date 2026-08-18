import os
import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.trading.repository import TradingRepository
from backend.trading.schemas import DataJobCreateRequest
from backend.trading.services.data_job_service import DataJobService
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
    market_data_service = MarketDataService(repo, provider=None)
    monkeypatch.setattr(router_mod, "market_data_service", market_data_service)
    monkeypatch.setattr(
        router_mod,
        "data_job_service",
        DataJobService(repo, market_data_service, router_mod.settings),
        raising=False,
    )
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
    loop = asyncio.get_running_loop()
    original_run_in_executor = loop.run_in_executor

    def forbid_inline_execution(*args, **kwargs):
        raise AssertionError("Web POST must not execute data jobs")

    loop.run_in_executor = forbid_inline_execution
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        try:
            resp = await ac.post("/api/trading/data-jobs", json={
                "job_type": "update_bars", "trade_date": "2026-07-20",
            })
        finally:
            loop.run_in_executor = original_run_in_executor
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "QUEUED"
    import backend.trading.router as router_mod
    persisted = router_mod.trading_repo.get_job(data["job_id"])
    assert persisted["status"] == "QUEUED"
    assert persisted["attempts"] == 0


@pytest.mark.asyncio
async def test_get_data_job():
    import backend.trading.router as router_mod

    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/api/trading/data-jobs", json={
            "job_type": "update_bars", "trade_date": "2026-07-20"})
        jid = r1.json()["job_id"]
        claimed = router_mod.trading_repo.claim_next_job(
            owner_id="api-test", now=now
        )
        assert claimed is not None
        assert router_mod.trading_repo.acquire_job_execution_lease(
            jid,
            owner_id="api-test",
            expected_attempt=claimed["attempts"],
            now=now,
            expires_at=datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc),
        ) is not None
        router_mod.trading_repo.complete_job(
            jid,
            result={"updated": 3},
            owner_id="api-test",
            expected_attempt=claimed["attempts"],
            now=now,
        )
        r2 = await ac.get(f"/api/trading/data-jobs/{jid}")
    assert r2.status_code == 200, r2.text
    assert r2.json() == {
        "id": jid,
        "job_type": "update_bars",
        "job_key": r1.json()["job_key"],
        "status": "SUCCEEDED",
        "progress": 1.0,
        "attempts": 1,
        "created_at": r2.json()["created_at"],
        "started_at": r2.json()["started_at"],
        "finished_at": r2.json()["finished_at"],
        "result_json": {"updated": 3},
        "error_json": None,
    }


@pytest.mark.asyncio
async def test_get_data_job_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/trading/data-jobs/99999")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "任务 99999 不存在"}


@pytest.mark.asyncio
async def test_repeated_terminal_job_returns_actual_status():
    import backend.trading.router as router_mod

    payload = {"job_type": "validate_data", "trade_date": "2026-07-31"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post("/api/trading/data-jobs", json=payload)
        router_mod.trading_repo.update_job(
            first.json()["job_id"],
            status="FAILED",
            error={"code": "PROVIDER_UNAVAILABLE"},
        )
        repeated = await client.post("/api/trading/data-jobs", json=payload)
        fetched = await client.get(
            f"/api/trading/data-jobs/{first.json()['job_id']}"
        )

    assert repeated.json()["job_id"] == first.json()["job_id"]
    assert repeated.json()["status"] == "FAILED"
    assert fetched.json()["result_json"] is None
    assert fetched.json()["error_json"] == {
        "code": "PROVIDER_UNAVAILABLE"
    }


@pytest.mark.asyncio
async def test_data_job_key_is_stable_for_normalized_code_order():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post("/api/trading/data-jobs", json={
            "job_type": "update_bars",
            "trade_date": "2026-07-31",
            "stock_codes": ["600000", "000001"],
        })
        second = await client.post("/api/trading/data-jobs", json={
            "job_type": "update_bars",
            "trade_date": "2026-07-31",
            "stock_codes": ["000001.SZ", "600000.SH"],
        })

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert first.json()["job_key"] == second.json()["job_key"]


def test_default_data_job_date_uses_shanghai_timezone():
    import backend.trading.router as router_mod

    request, _ = router_mod.build_data_job_request(
        DataJobCreateRequest(job_type="validate_data"),
        now=datetime(2026, 7, 30, 16, 30, tzinfo=timezone.utc),
    )

    assert request["trade_date"] == "2026-07-31"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    (
        {"job_type": "update_bars", "trade_date": "2026-02-30"},
        {"job_type": "update_bars", "stock_codes": ["invalid"]},
        {
            "job_type": "backfill_bars",
            "start_date": "2026-07-31",
            "end_date": "2026-07-01",
        },
    ),
)
async def test_create_data_job_rejects_invalid_request(payload):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/trading/data-jobs", json=payload)

    assert response.status_code == 422


def test_configure_runtime_uses_one_provider_instance(monkeypatch, tmp_path):
    import backend.trading.router as router_mod

    fake_provider = object()
    fake_settings = SimpleNamespace(
        db_path=str(tmp_path / "runtime.db"),
        trading_data_max_missing_ratio=0.03,
        trading_benchmark_codes="000300.SH,000905.SH",
    )
    run_migrations(fake_settings.db_path)
    for name in (
        "runtime_provider",
        "trading_repo",
        "pool_service",
        "market_data_service",
        "data_job_service",
        "account_service",
        "execution_service",
        "portfolio_service",
        "strategy_service",
        "plan_service",
        "backtest_service",
        "review_service",
    ):
        monkeypatch.setattr(
            router_mod, name, getattr(router_mod, name, None), raising=False
        )

    router_mod.configure_runtime(fake_settings, provider=fake_provider)

    assert router_mod.runtime_provider is fake_provider
    assert router_mod.market_data_service.provider is fake_provider
    assert (
        router_mod.data_job_service.market_data_service
        is router_mod.market_data_service
    )
    assert router_mod.plan_service.market_data_service.provider is fake_provider


@pytest.mark.asyncio
async def test_get_stock_pool_version_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/trading/stock-pools/99999")
    assert resp.status_code == 404
