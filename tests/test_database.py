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
        "per_capital_pnl": None,
        "per_capital_position": None,
        "total_fund": 1911.55,
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
        "per_capital_pnl": None,
        "per_capital_position": None,
        "total_fund": 1911.55,
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
            "per_capital_pnl": None, "per_capital_position": None,
            "total_fund": 100.0,
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
            "per_capital_pnl": None, "per_capital_position": None,
            "total_fund": 100.0,
        })
    dates = db.get_all_dates()
    assert "2026-04-25" in dates
    assert "2026-04-27" in dates


# ---- query_streak_stats ----

def _insert_record(db, date, rank, stock_code, stock_name="测试股", sector_tags='[]'):
    db.insert_record({
        "date": date, "rank": rank, "stock_name": stock_name,
        "stock_code": stock_code, "heat_value": 100.0,
        "sector_tags": sector_tags, "price_change_pct": 1.0,
        "turnover_amount": 10.0, "holders_today": 10,
        "holders_yesterday": 8, "price_action": "",
        "per_capital_pnl": None, "per_capital_position": None,
        "total_fund": 100.0,
    })


def test_streak_stats_empty(db):
    """无数据时返回空 streaks。"""
    result = db.query_streak_stats(days=30, min_streak=2)
    assert result["streaks"] == []
    assert "start_date" in result
    assert "end_date" in result


def test_streak_stats_with_data(db):
    """000001 连续 3 天 (rank 5,3,1) -> is_dark_horse=True。
    000002 只有 1 天 -> 不满足 min_streak=2 被过滤。"""
    _insert_record(db, "2026-05-19", 5, "000001", "测试A")
    _insert_record(db, "2026-05-20", 3, "000001", "测试A")
    _insert_record(db, "2026-05-21", 1, "000001", "测试A")
    _insert_record(db, "2026-05-21", 2, "000002", "测试B")

    result = db.query_streak_stats(days=30, min_streak=2)
    streaks = result["streaks"]
    assert len(streaks) == 1
    s = streaks[0]
    assert s["stock_code"] == "000001"
    assert s["streak_days"] == 3
    assert s["is_dark_horse"] is True
    assert s["ranks"][-1] == 5
    assert s["ranks"][0] == 1
    assert len(s["dates"]) == 3
    assert s["latest_change"] is not None


def test_streak_stats_min_streak_filter(db):
    """000001 连续 2 天, 000002 只有 1 天。min_streak=2 只返回 000001。"""
    _insert_record(db, "2026-05-20", 3, "000001", "测试A")
    _insert_record(db, "2026-05-21", 1, "000001", "测试A")
    _insert_record(db, "2026-05-21", 2, "000002", "测试B")

    result = db.query_streak_stats(days=30, min_streak=2)
    streaks = result["streaks"]
    assert len(streaks) == 1
    assert streaks[0]["stock_code"] == "000001"
    assert streaks[0]["streak_days"] == 2


# ---- query_stock_history ----

def test_stock_history_not_found(db):
    """查不存在的代码返回空列表。"""
    result = db.query_stock_history("999999")
    assert result["stock_code"] == "999999"
    assert result["stock_name"] == ""
    assert result["records"] == []
    assert result["lhb_signals"] == []
    assert result["lhb_trading_desk"] == []


def test_stock_history_with_records(db):
    """插入记录后查询正常，sector_tags 和 concept_tags 解析为 list。"""
    _insert_record(db, "2026-05-20", 1, "000001", "测试A", '["新能源"]')

    # 插入 lhb_signals
    db.upsert_lhb_signals([{
        "date": "2026-05-20", "stock_code": "000001", "stock_name": "测试A",
        "signal_type": "foreign", "close_price": 10.0, "change_rate": 5.0,
        "buy_amt": 100.0, "sell_amt": 50.0, "net_amt": 50.0,
        "inst_count": 3, "concept_tags": '["AI","芯片"]',
    }])

    # 插入 lhb_trading_desk
    db.upsert_lhb_trading_desk([{
        "date": "2026-05-20", "stock_code": "000001", "stock_name": "测试A",
        "side": "buy", "seat_index": 0, "dept_name": "机构专用",
        "buy_amt": 100.0, "sell_amt": 0.0, "net_amt": 100.0,
    }])

    result = db.query_stock_history("000001")
    assert result["stock_code"] == "000001"
    assert result["stock_name"] == "测试A"
    assert len(result["records"]) == 1
    assert result["records"][0]["sector_tags"] == ["新能源"]
    assert len(result["lhb_signals"]) == 1
    assert result["lhb_signals"][0]["concept_tags"] == ["AI", "芯片"]
    assert len(result["lhb_trading_desk"]) == 1
