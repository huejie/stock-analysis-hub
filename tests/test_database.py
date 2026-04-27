import os
import sqlite3
import pytest
from backend.database import Database

TEST_DB = "data/test_stock.db"


@pytest.fixture
def db():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    database = Database(TEST_DB)
    yield database
    # 关闭所有连接后清理
    database._conn = None
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


def test_init_creates_table(db):
    result = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    names = [r["name"] for r in result]
    assert "stock_records" in names


def test_insert_and_query_record(db):
    record = {
        "date": "2026-04-27",
        "rank": 1,
        "stock_name": "圣阳股份",
        "stock_code": "002580",
        "heat_value": 1358.33,
        "sector_tags": '["液冷储能"]',
        "price_change_pct": 4.0,
        "turnover_amount": 44.0,
        "holders_today": 42,
        "holders_yesterday": 35,
        "price_action": "上午震荡回落-下午大幅冲高回落",
    }
    db.insert_record(record)
    rows = db.query_by_date("2026-04-27")
    assert len(rows) == 1
    assert rows[0]["stock_name"] == "圣阳股份"
    assert rows[0]["stock_code"] == "002580"
    assert rows[0]["heat_value"] == 1358.33


def test_unique_constraint(db):
    record = {
        "date": "2026-04-27",
        "rank": 1,
        "stock_name": "圣阳股份",
        "stock_code": "002580",
        "heat_value": 1358.33,
        "sector_tags": '["液冷储能"]',
        "price_change_pct": 4.0,
        "turnover_amount": 44.0,
        "holders_today": 42,
        "holders_yesterday": 35,
        "price_action": "上午震荡回落",
    }
    db.insert_record(record)
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_record(record)


def test_query_date_range(db):
    for date in ["2026-04-25", "2026-04-26", "2026-04-27"]:
        db.insert_record({
            "date": date, "rank": 1, "stock_name": "测试",
            "stock_code": "000001", "heat_value": 100.0,
            "sector_tags": '[]', "price_change_pct": 1.0,
            "turnover_amount": 10.0, "holders_today": 10,
            "holders_yesterday": 8, "price_action": "",
        })
    rows = db.query_date_range("2026-04-25", "2026-04-26")
    assert len(rows) == 2


def test_get_all_dates(db):
    for date in ["2026-04-25", "2026-04-27"]:
        db.insert_record({
            "date": date, "rank": 1, "stock_name": "测试",
            "stock_code": "000001", "heat_value": 100.0,
            "sector_tags": '[]', "price_change_pct": 1.0,
            "turnover_amount": 10.0, "holders_today": 10,
            "holders_yesterday": 8, "price_action": "",
        })
    dates = db.get_all_dates()
    assert "2026-04-25" in dates
    assert "2026-04-27" in dates
