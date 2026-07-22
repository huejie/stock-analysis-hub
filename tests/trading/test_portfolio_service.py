import os
from datetime import date
import pytest
from backend.trading.services.portfolio_service import PortfolioService
from backend.trading.repository import TradingRepository
from backend.trading.migrations import run_migrations
from backend.trading.domain import DailyBar

TEST_DB = "data/test_portfolio_svc.db"


@pytest.fixture
def setup():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    repo = TradingRepository(TEST_DB)
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    yield repo, acc
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


def _bar(code, d, close):
    return DailyBar(code=code, trade_date=d, open=close, high=close, low=close,
                    close=close, volume=1000, source="test")


def test_compute_equity_snapshot_no_positions(setup):
    """无持仓:market_value=0, total_equity=cash。"""
    repo, acc = setup
    svc = PortfolioService(repo)
    snap = svc.compute_equity_snapshot(acc["id"], date(2026, 7, 22))
    assert snap["market_value"] == 0
    assert snap["total_equity"] == 100000
    assert snap["exposure"] == 0
    assert snap["drawdown"] == 0


def test_compute_equity_snapshot_with_positions(setup):
    """有持仓:market_value = SUM(qty * close)。"""
    repo, acc = setup
    # 持仓:1000 股 @ 成本 10,收盘价 12
    repo.upsert_position(account_id=acc["id"], stock_code="000001.SZ", quantity=1000,
                         available_quantity=1000, average_cost=10)
    repo.upsert_daily_bars([_bar("000001.SZ", date(2026, 7, 22), 12.0)])
    svc = PortfolioService(repo)
    snap = svc.compute_equity_snapshot(acc["id"], date(2026, 7, 22))
    # market_value = 1000 * 12 = 12000, cash=100000, equity=112000
    assert snap["market_value"] == 12000
    assert snap["total_equity"] == 112000
    assert snap["exposure"] == pytest.approx(12000 / 112000)


def test_equity_snapshot_peak_monotonic(setup):
    """peak_equity 单调非递减。"""
    repo, acc = setup
    svc = PortfolioService(repo)
    # Day 1: equity=100000
    snap1 = svc.compute_and_save_equity_snapshot(acc["id"], date(2026, 7, 21))
    assert snap1["peak_equity"] == 100000
    # Day 2: 持仓涨,equity=105000
    repo.upsert_position(account_id=acc["id"], stock_code="000001.SZ", quantity=1000,
                         available_quantity=1000, average_cost=10)
    repo.upsert_daily_bars([_bar("000001.SZ", date(2026, 7, 22), 15.0)])
    snap2 = svc.compute_and_save_equity_snapshot(acc["id"], date(2026, 7, 22))
    assert snap2["peak_equity"] == 115000  # 100000 cash + 15000 mv
    # Day 3: 持仓跌,equity=108000, peak 不变
    repo.upsert_daily_bars([_bar("000001.SZ", date(2026, 7, 23), 8.0)])
    snap3 = svc.compute_and_save_equity_snapshot(acc["id"], date(2026, 7, 23))
    assert snap3["peak_equity"] == 115000  # peak 不降
    assert snap3["drawdown"] > 0


def test_drawdown_computation(setup):
    """drawdown = (peak - equity) / peak。"""
    repo, acc = setup
    repo.upsert_position(account_id=acc["id"], stock_code="000001.SZ", quantity=1000,
                         available_quantity=1000, average_cost=10)
    repo.upsert_daily_bars([_bar("000001.SZ", date(2026, 7, 22), 15.0)])
    # 先建一个 peak=115000 的快照
    repo.upsert_equity_snapshot(account_id=acc["id"], trade_date="2026-07-21",
                                cash=100000, market_value=15000, total_equity=115000,
                                exposure=0.13, peak_equity=115000, drawdown=0)
    svc = PortfolioService(repo)
    # 当前 equity=108000(100000 cash + 1000*8)
    repo.upsert_daily_bars([_bar("000001.SZ", date(2026, 7, 22), 8.0)])
    snap = svc.compute_and_save_equity_snapshot(acc["id"], date(2026, 7, 22))
    # drawdown = (115000 - 108000) / 115000 ≈ 0.0609
    assert snap["drawdown"] == pytest.approx(0.0609, abs=0.001)


def test_exposure_aggregation_by_sector(setup):
    """行业暴露聚合。"""
    repo, acc = setup
    repo.upsert_position(account_id=acc["id"], stock_code="000001.SZ", quantity=1000,
                         available_quantity=1000, average_cost=10, stock_name="平安")
    repo.upsert_position(account_id=acc["id"], stock_code="600000.SH", quantity=500,
                         available_quantity=500, average_cost=5, stock_name="浦发")
    repo.upsert_daily_bars([
        _bar("000001.SZ", date(2026, 7, 22), 12.0),
        _bar("600000.SH", date(2026, 7, 22), 6.0),
    ])
    svc = PortfolioService(repo)
    exposure = svc.compute_sector_exposure(acc["id"], date(2026, 7, 22))
    # 总 market_value = 12000 + 3000 = 15000, equity = 115000
    # 无 sector 标签时按"未知"聚合
    total_pct = sum(s["exposure_pct"] for s in exposure)
    assert total_pct == pytest.approx(15000 / 115000, abs=0.01)


def test_compute_equity_missing_bar_blocks(setup):
    """持仓缺行情 -> 阻断(spec §7.4:持仓缺失必须阻断)。"""
    repo, acc = setup
    repo.upsert_position(account_id=acc["id"], stock_code="000001.SZ", quantity=1000,
                         available_quantity=1000, average_cost=10)
    # 不插入行情
    svc = PortfolioService(repo)
    with pytest.raises(Exception, match="000001.SZ"):
        svc.compute_equity_snapshot(acc["id"], date(2026, 7, 22))
