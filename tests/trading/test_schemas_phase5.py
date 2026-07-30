"""Phase 5 schema 测试(spec §11.2 backtests / reviews)。

覆盖请求/响应模型的校验与默认值。
"""
import pytest
from pydantic import ValidationError

from backend.trading.schemas import (
    BacktestCreateRequest,
    BacktestRunResponse,
    BacktestTradeResponse,
    FeeParams,
    ReviewSummaryResponse,
)


def test_backtest_create_request_defaults():
    req = BacktestCreateRequest(
        strategy_version_id=1, stock_pool_version_id=2,
        start_date="2026-06-01", end_date="2026-07-22",
    )
    assert req.initial_equity == 100_000
    assert req.fee_params is None


def test_backtest_create_request_with_fee_params():
    req = BacktestCreateRequest(
        strategy_version_id=1, stock_pool_version_id=2,
        start_date="2026-06-01", end_date="2026-07-22",
        fee_params=FeeParams(commission_rate=0.001, min_commission=5,
                             stamp_tax=0.001, slippage=0.05),
    )
    assert req.fee_params.commission_rate == 0.001


def test_backtest_create_rejects_nonpositive_equity():
    with pytest.raises(ValidationError):
        BacktestCreateRequest(
            strategy_version_id=1, stock_pool_version_id=2,
            start_date="2026-06-01", end_date="2026-07-22",
            initial_equity=0,
        )


def test_fee_params_all_optional():
    fp = FeeParams()
    assert fp.commission_rate is None
    assert fp.slippage is None


def test_fee_params_rejects_negative():
    with pytest.raises(ValidationError):
        FeeParams(commission_rate=-0.01)
    with pytest.raises(ValidationError):
        FeeParams(slippage=-1)


def test_fee_params_rejects_oversized_rate():
    """commission_rate/stamp_tax 上限 0.05。"""
    with pytest.raises(ValidationError):
        FeeParams(commission_rate=0.5)


def test_backtest_trade_response_defaults():
    t = BacktestTradeResponse(id=1, stock_code="000001.SZ", signal_date="2026-06-01")
    assert t.entry_date is None
    assert t.details == {}


def test_backtest_run_response_shape():
    r = BacktestRunResponse(
        id=1, job_id=10, strategy_version_id=1, stock_pool_version_id=2,
        start_date="2026-06-01", end_date="2026-07-22", initial_equity=100000,
        fee_params={"commission_rate": 0.0003}, status="SUCCEEDED",
        created_at="2026-07-22T10:00:00",
    )
    assert r.metrics is None
    assert r.trades == []
    assert r.equity_curve is None


def test_review_summary_response_shape():
    r = ReviewSummaryResponse(
        account_id=1, period=30, start_date="2026-06-01", end_date="2026-07-01",
        trade_count=2, win_rate=0.5, avg_win_r=1.0, avg_loss_r=1.0,
        expectancy=0.0, profit_factor=1.0, max_drawdown=0.05,
        execution_rate=1.0, executed_count=2, total_signals=2,
        gross_profit=1000.0, gross_loss=1000.0,
    )
    assert r.trade_count == 2
    assert r.profit_factor == 1.0


def test_review_summary_response_allows_null_profit_factor():
    """profit_factor=None(inf 全胜)允许。"""
    r = ReviewSummaryResponse(
        account_id=1, period=30, start_date="2026-06-01", end_date="2026-07-01",
        trade_count=1, win_rate=1.0, avg_win_r=2.0, avg_loss_r=0.0,
        expectancy=2.0, profit_factor=None, max_drawdown=0.0,
        execution_rate=1.0, executed_count=1, total_signals=1,
        gross_profit=1000.0, gross_loss=0.0,
    )
    assert r.profit_factor is None
