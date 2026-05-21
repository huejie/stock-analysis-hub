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


# ---- /api/stats/streak ----

@pytest.mark.asyncio
async def test_streak_stats_api(setup_test_db):
    """插入数据后 GET /api/stats/streak 返回正常。"""
    db_instance = Database(TEST_DB)
    # 插入连续 3 天的数据（今天、昨天、前天）
    from datetime import date, timedelta
    today = date.today()
    for i in range(3):
        d = (today - timedelta(days=i)).isoformat()
        db_instance.insert_records([{
            "date": d, "rank": i + 1, "stock_name": "测试A",
            "stock_code": "000001", "heat_value": 100.0,
            "sector_tags": '[]', "price_change_pct": 1.0,
            "turnover_amount": 10.0, "holders_today": 10,
            "holders_yesterday": 8, "price_action": "",
            "per_capital_pnl": None, "per_capital_position": None,
            "total_fund": 100.0,
        }])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/stats/streak", params={"days": 30, "min_streak": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert "streaks" in data
    assert len(data["streaks"]) >= 1
    assert data["streaks"][0]["stock_code"] == "000001"


# ---- /api/stocks/{code}/history ----

@pytest.mark.asyncio
async def test_stock_history_api_not_found(setup_test_db):
    """GET /api/stocks/999999/history 返回 404。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/stocks/999999/history")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_stock_history_api_with_data(setup_test_db):
    """插入数据后查询正常。"""
    db_instance = Database(TEST_DB)
    db_instance.insert_records([{
        "date": "2026-05-20", "rank": 1, "stock_name": "测试A",
        "stock_code": "000001", "heat_value": 100.0,
        "sector_tags": '["新能源"]', "price_change_pct": 1.0,
        "turnover_amount": 10.0, "holders_today": 10,
        "holders_yesterday": 8, "price_action": "",
        "per_capital_pnl": None, "per_capital_position": None,
        "total_fund": 100.0,
    }])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/stocks/000001/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["stock_code"] == "000001"
    assert data["stock_name"] == "测试A"
    assert len(data["records"]) == 1
    assert data["records"][0]["sector_tags"] == ["新能源"]


# ---- /api/reports/daily ----

@pytest.mark.asyncio
async def test_daily_report_api(setup_test_db):
    """无数据日期返回空 sections。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/reports/daily", params={"date_str": "2026-01-01"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["date"] == "2026-01-01"
    assert data["sections"] == []


# ---- /api/lhb/backtest ----

@pytest.mark.asyncio
async def test_backtest_api_empty(setup_test_db):
    """GET /api/lhb/backtest 无数据时返回 total_signals=0。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/lhb/backtest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_signals"] == 0
