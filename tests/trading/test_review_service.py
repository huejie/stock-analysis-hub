"""ReviewService 测试(spec §11.2 /reviews/summary + §14.3 复盘指标)。

覆盖:
- 平仓配对(FIFO):多笔 BUY + SELL 配对出已平仓交易。
- win_rate / avg_r / expectancy / profit_factor / max_drawdown / execution_rate。
- R 倍数需关联 plan_item.stop_price(缺失则不计入 R 口径)。
- 无数据 / 区间过滤 / period 校验 / 账户不存在。
"""
import os
from datetime import date, timedelta

import pytest

from backend.trading.migrations import run_migrations
from backend.trading.repository import TradingRepository
from backend.trading.services.review_service import ReviewService

TEST_DB = "data/test_review_service.db"


@pytest.fixture
def setup():
    """脚手架:临时 DB + 账户 + 一个 plan_run(含 CONDITIONAL_BUY items)。"""
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    repo = TradingRepository(TEST_DB)
    account = repo.create_account(name="main", initial_equity=100_000,
                                  cash_balance=100_000)
    # 构造一个 plan_run + 2 个 CONDITIONAL_BUY items(带 stop_price)
    created = repo.create_plan_run(
        run_key="rk1", account_id=account["id"], signal_date="2026-07-01",
        target_trade_date="2026-07-02", stock_pool_version_id=1,
        strategy_version_id=1, status="PUBLISHED",
        account_snapshot_json={}, data_snapshot_hash="h",
    )
    run_id = created["id"]
    item1 = repo.create_plan_item(
        plan_run_id=run_id, stock_code="000001.SZ", action="CONDITIONAL_BUY",
        rank_no=1, trigger_price=10.5, do_not_chase_price=10.82,
        stop_price=9.98, target_2r_price=11.60, suggested_quantity=1000,
    )
    item2 = repo.create_plan_item(
        plan_run_id=run_id, stock_code="600519.SH", action="CONDITIONAL_BUY",
        rank_no=2, trigger_price=1600.0, do_not_chase_price=1648.0,
        stop_price=1520.0, target_2r_price=1760.0, suggested_quantity=10,
    )
    svc = ReviewService(repo)
    yield repo, svc, account, run_id, item1, item2
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


def _exec(repo, account, code, side, d, price, qty, pid, ceid):
    return repo.create_execution(
        account_id=account["id"], stock_code=code, side=side,
        trade_date=d, price=price, quantity=qty,
        client_execution_id=ceid, plan_item_id=pid,
    )


# ===================================================================
# 正常摘要:有平仓 + 有未平仓 + 指标完整
# ===================================================================

def test_summary_with_closed_trades(setup):
    repo, svc, account, run_id, item1, item2 = setup
    today = date.today()
    d1 = (today - timedelta(days=10)).isoformat()
    d2 = (today - timedelta(days=5)).isoformat()

    # item1:BUY @10, SELL @11 (盈利,R=(11-10)/(10-9.98)=50? stop 很近 → R 很大)
    # 改用更合理的 stop:这里 item1.stop=9.98,entry≈10 → per_share_risk≈0.02
    # 为了测试可控,直接用 plan_item 已设的 stop_price。
    _exec(repo, account, "000001.SZ", "BUY", d1, 10.0, 1000, item1["id"], "b1")
    _exec(repo, account, "000001.SZ", "SELL", d2, 11.0, 1000, item1["id"], "s1")
    # item2:BUY @1600, SELL @1500 (亏损)
    _exec(repo, account, "600519.SH", "BUY", d1, 1600.0, 10, item2["id"], "b2")
    _exec(repo, account, "600519.SH", "SELL", d2, 1500.0, 10, item2["id"], "s2")

    # 注入净值快照(含 drawdown)
    repo.upsert_equity_snapshot(
        account_id=account["id"], trade_date=d1, cash=90000, market_value=10000,
        total_equity=100000, exposure=0.1, peak_equity=100000, drawdown=0.0,
    )
    repo.upsert_equity_snapshot(
        account_id=account["id"], trade_date=d2, cash=90000, market_value=9500,
        total_equity=99500, exposure=0.095, peak_equity=100000, drawdown=0.005,
    )

    s = svc.get_summary(account_id=account["id"], period=30)
    assert s["account_id"] == account["id"]
    assert s["trade_count"] == 2
    assert s["win_rate"] == 0.5
    # item1 盈利 1000,item2 亏损 1000
    assert s["gross_profit"] == pytest.approx(1000.0)
    assert s["gross_loss"] == pytest.approx(1000.0)
    # PF = 1000/1000 = 1
    assert s["profit_factor"] == pytest.approx(1.0)
    # max drawdown = 0.005
    assert s["max_drawdown"] == pytest.approx(0.005)
    # R 口径:item1 entry 10 stop 9.98 → risk 0.02,exit 11 → R=50
    # item2 entry 1600 stop 1520 → risk 80,exit 1500 → R=-1.25
    assert s["avg_win_r"] == pytest.approx(50.0)
    assert s["avg_loss_r"] == pytest.approx(1.25)
    # 执行率:2 个 CONDITIONAL_BUY items 都有 BUY 成交 → 2/2
    assert s["execution_rate"] == 1.0
    assert s["executed_count"] == 2
    assert s["total_signals"] == 2


def test_fifo_partial_close(setup):
    """部分平仓:FIFO 切分多笔 BUY。"""
    repo, svc, account, run_id, item1, item2 = setup
    today = date.today()
    d1 = (today - timedelta(days=10)).isoformat()
    d2 = (today - timedelta(days=8)).isoformat()
    d3 = (today - timedelta(days=5)).isoformat()

    # 两笔 BUY 不同价,一笔 SELL 部分平仓
    _exec(repo, account, "000001.SZ", "BUY", d1, 10.0, 600, item1["id"], "b1")
    _exec(repo, account, "000001.SZ", "BUY", d2, 11.0, 400, item1["id"], "b2")
    # SELL 800:FIFO 先消耗 b1 的 600 + b2 的 200
    _exec(repo, account, "000001.SZ", "SELL", d3, 12.0, 800, item1["id"], "s1")

    s = svc.get_summary(account_id=account["id"], period=30)
    assert s["trade_count"] == 2
    # b1 600 股: (12-10)*600 = 1200
    # b2 200 股: (12-11)*200 = 200
    assert s["gross_profit"] == pytest.approx(1400.0)


def test_r_multiple_none_without_stop_price(setup):
    """BUY 无 plan_item_id → R 倍数为 None,不计入 R 口径。"""
    repo, svc, account, run_id, item1, item2 = setup
    today = date.today()
    d1 = (today - timedelta(days=10)).isoformat()
    d2 = (today - timedelta(days=5)).isoformat()
    # BUY 无 plan_item_id(人工录入)
    _exec(repo, account, "000001.SZ", "BUY", d1, 10.0, 1000, None, "b1")
    _exec(repo, account, "000001.SZ", "SELL", d2, 11.0, 1000, None, "s1")

    s = svc.get_summary(account_id=account["id"], period=30)
    assert s["trade_count"] == 1  # 仍计为平仓交易
    assert s["win_rate"] == 1.0
    # R 口径无数据 → avg_win_r / expectancy 为 0
    assert s["avg_win_r"] == 0.0
    assert s["expectancy"] == 0.0


def test_empty_account_returns_zeros(setup):
    repo, svc, account, run_id, item1, item2 = setup
    s = svc.get_summary(account_id=account["id"], period=30)
    assert s["trade_count"] == 0
    assert s["win_rate"] == 0.0
    assert s["max_drawdown"] == 0.0
    assert s["execution_rate"] == 0.0
    assert s["total_signals"] == 2  # 有 2 个 CONDITIONAL_BUY items
    assert s["executed_count"] == 0


def test_execution_rate_partial(setup):
    """只执行了 1 个 CONDITIONAL_BUY → 执行率 0.5。"""
    repo, svc, account, run_id, item1, item2 = setup
    today = date.today()
    d1 = (today - timedelta(days=5)).isoformat()
    _exec(repo, account, "000001.SZ", "BUY", d1, 10.0, 1000, item1["id"], "b1")

    s = svc.get_summary(account_id=account["id"], period=30)
    assert s["executed_count"] == 1
    assert s["total_signals"] == 2
    assert s["execution_rate"] == pytest.approx(0.5)


def test_period_filters_out_old_executions(setup):
    """period=5 时,10 天前的成交不计入(但 plan_items 仍按 signal_date 过滤)。"""
    repo, svc, account, run_id, item1, item2 = setup
    today = date.today()
    old = (today - timedelta(days=20)).isoformat()
    _exec(repo, account, "000001.SZ", "BUY", old, 10.0, 1000, item1["id"], "b_old")

    # plan_run signal_date 是 2026-07-01(远早于今天),不在近 5 天窗口内
    s = svc.get_summary(account_id=account["id"], period=5)
    # 成交在 20 天前 → 不计入;plan_run signal_date 也过滤掉 → 0 signals
    assert s["executed_count"] == 0
    assert s["total_signals"] == 0


def test_invalid_period_raises(setup):
    repo, svc, account, *_ = setup
    with pytest.raises(ValueError, match="period"):
        svc.get_summary(account_id=account["id"], period=0)


def test_nonexistent_account_raises(setup):
    repo, svc, *_ = setup
    with pytest.raises(ValueError, match="账户"):
        svc.get_summary(account_id=99999, period=30)


def test_default_period_is_30(setup):
    repo, svc, account, *_ = setup
    s = svc.get_summary(account_id=account["id"])
    assert s["period"] == 30


def test_profit_factor_infinite_when_no_losses(setup):
    """只有盈利交易、无亏损 → profit_factor = None(inf)。"""
    repo, svc, account, run_id, item1, item2 = setup
    today = date.today()
    d1 = (today - timedelta(days=10)).isoformat()
    d2 = (today - timedelta(days=5)).isoformat()
    _exec(repo, account, "000001.SZ", "BUY", d1, 10.0, 1000, item1["id"], "b1")
    _exec(repo, account, "000001.SZ", "SELL", d2, 12.0, 1000, item1["id"], "s1")
    s = svc.get_summary(account_id=account["id"], period=30)
    assert s["profit_factor"] is None  # inf → None
