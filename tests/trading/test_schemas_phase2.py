import pytest
from pydantic import ValidationError
from backend.trading.schemas import (
    AccountCreateRequest,
    AccountUpdateRequest,
    AccountResponse,
    PositionResponse,
    ExecutionCreateRequest,
    ExecutionResponse,
    EquitySnapshotResponse,
)


def test_account_create_defaults():
    req = AccountCreateRequest(name="main", initial_equity=100000, cash_balance=100000)
    assert req.risk_per_trade == 0.005
    assert req.max_single_position == 0.15
    assert req.max_positions == 5


def test_account_create_rejects_negative_equity():
    with pytest.raises(ValidationError):
        AccountCreateRequest(name="x", initial_equity=-100, cash_balance=100)


def test_account_create_rejects_empty_name():
    with pytest.raises(ValidationError):
        AccountCreateRequest(name="", initial_equity=100, cash_balance=100)


def test_account_update_partial():
    """PUT 只更新传入的字段。"""
    req = AccountUpdateRequest(cash_balance=80000)
    assert req.cash_balance == 80000
    assert req.risk_per_trade is None


def test_execution_create_buy():
    req = ExecutionCreateRequest(
        account_id=1, stock_code="000001.SZ", side="BUY",
        trade_date="2026-07-22", price=10.5, quantity=1000,
        commission=5.0, tax=0, client_execution_id="exec-001",
    )
    assert req.side == "BUY"
    assert req.client_execution_id == "exec-001"


def test_execution_create_requires_client_execution_id():
    with pytest.raises(ValidationError):
        ExecutionCreateRequest(
            account_id=1, stock_code="000001.SZ", side="BUY",
            trade_date="2026-07-22", price=10.5, quantity=1000,
        )


def test_execution_create_rejects_zero_price():
    with pytest.raises(ValidationError):
        ExecutionCreateRequest(
            account_id=1, stock_code="000001.SZ", side="BUY",
            trade_date="2026-07-22", price=0, quantity=100,
            client_execution_id="x",
        )


def test_execution_create_rejects_non_100_quantity():
    """A 股必须 100 股整数倍。"""
    with pytest.raises(ValidationError):
        ExecutionCreateRequest(
            account_id=1, stock_code="000001.SZ", side="BUY",
            trade_date="2026-07-22", price=10, quantity=150,
            client_execution_id="x",
        )
