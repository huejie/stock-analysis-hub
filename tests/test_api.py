import os
import pytest
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.database import Database

TEST_DB = "data/test_api.db"


@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    db = Database(TEST_DB)
    monkeypatch.setattr("backend.main.db", db)
    yield
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


@pytest.mark.asyncio
async def test_get_dates_empty():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dates")
    assert resp.status_code == 200
    assert resp.json()["dates"] == []


@pytest.mark.asyncio
async def test_save_and_query_records():
    payload = {
        "date": "2026-04-27",
        "records": [
            {
                "rank": 1,
                "stock_name": "圣阳股份",
                "stock_code": "002580",
                "heat_value": 1358.33,
                "sector_tags": ["液冷储能"],
                "price_change_pct": 4.0,
                "turnover_amount": 44.0,
                "holders_today": 42,
                "holders_yesterday": 35,
                "price_action": "上午震荡回落",
            }
        ]
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 保存
        resp = await ac.post("/api/records", json=payload)
        assert resp.status_code == 200

        # 查询
        resp = await ac.get("/api/records", params={"date": "2026-04-27"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["stock_name"] == "圣阳股份"

        # 日期列表
        resp = await ac.get("/api/dates")
        assert resp.status_code == 200
        assert "2026-04-27" in resp.json()["dates"]


@pytest.mark.asyncio
async def test_query_range():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for date in ["2026-04-25", "2026-04-26"]:
            payload = {
                "date": date,
                "records": [{
                    "rank": 1, "stock_name": "测试", "stock_code": "000001",
                    "heat_value": 100.0, "sector_tags": [],
                    "price_change_pct": 1.0, "turnover_amount": 10.0,
                    "holders_today": 5, "holders_yesterday": 3, "price_action": "",
                }]
            }
            await ac.post("/api/records", json=payload)

        resp = await ac.get("/api/records/range", params={"start": "2026-04-25", "end": "2026-04-26"})
        assert resp.status_code == 200
        assert len(resp.json()) == 2
