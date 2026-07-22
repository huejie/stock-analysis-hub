import os
from datetime import date
import pytest
from backend.trading.repository import TradingRepository
from backend.trading.migrations import run_migrations

TEST_DB = "data/test_repo_phase2.db"


@pytest.fixture
def repo():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    yield TradingRepository(TEST_DB)
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


# ---- Account ----

def test_create_account(repo):
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    assert acc["id"] > 0
    assert acc["name"] == "main"
    assert acc["risk_per_trade"] == 0.005


def test_create_account_duplicate_name_raises(repo):
    repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    with pytest.raises(Exception):  # UNIQUE 约束
        repo.create_account(name="main", initial_equity=50000, cash_balance=50000)


def test_get_account(repo):
    created = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    fetched = repo.get_account(created["id"])
    assert fetched["name"] == "main"


def test_get_account_not_found(repo):
    assert repo.get_account(99999) is None


def test_list_accounts(repo):
    repo.create_account(name="a", initial_equity=100000, cash_balance=100000)
    repo.create_account(name="b", initial_equity=50000, cash_balance=50000)
    accounts = repo.list_accounts()
    assert len(accounts) == 2


def test_update_account(repo):
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    repo.update_account(acc["id"], {"cash_balance": 80000, "risk_per_trade": 0.003})
    fetched = repo.get_account(acc["id"])
    assert fetched["cash_balance"] == 80000
    assert fetched["risk_per_trade"] == 0.003


def test_update_account_ignores_initial_equity(repo):
    """initial_equity 不可改(服务层语义)。"""
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    repo.update_account(acc["id"], {"initial_equity": 999999})
    fetched = repo.get_account(acc["id"])
    assert fetched["initial_equity"] == 100000  # 未变


# ---- Position ----

def test_upsert_position_create(repo):
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    pos = repo.upsert_position(
        account_id=acc["id"], stock_code="000001.SZ", stock_name="平安",
        quantity=1000, available_quantity=0, average_cost=10.5,
    )
    assert pos["id"] > 0
    assert pos["quantity"] == 1000


def test_get_positions_by_account(repo):
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    repo.upsert_position(account_id=acc["id"], stock_code="000001.SZ", quantity=1000,
                         available_quantity=1000, average_cost=10)
    repo.upsert_position(account_id=acc["id"], stock_code="600000.SH", quantity=500,
                         available_quantity=500, average_cost=5)
    positions = repo.get_positions(acc["id"])
    assert len(positions) == 2


def test_get_position(repo):
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    repo.upsert_position(account_id=acc["id"], stock_code="000001.SZ", quantity=1000,
                         available_quantity=1000, average_cost=10)
    pos = repo.get_position(acc["id"], "000001.SZ")
    assert pos["stock_code"] == "000001.SZ"
    assert repo.get_position(acc["id"], "999999.SZ") is None


def test_delete_position(repo):
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    repo.upsert_position(account_id=acc["id"], stock_code="000001.SZ", quantity=1000,
                         available_quantity=1000, average_cost=10)
    repo.delete_position(acc["id"], "000001.SZ")
    assert repo.get_position(acc["id"], "000001.SZ") is None


# ---- Execution ----

def test_create_execution(repo):
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    exec_row = repo.create_execution(
        account_id=acc["id"], stock_code="000001.SZ", side="BUY",
        trade_date="2026-07-22", price=10.5, quantity=1000,
        commission=5, tax=0, client_execution_id="exec-1",
    )
    assert exec_row["id"] > 0
    assert exec_row["side"] == "BUY"


def test_create_execution_idempotent_on_client_id(repo):
    """相同 client_execution_id 返回已有,不新建。"""
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    e1 = repo.create_execution(
        account_id=acc["id"], stock_code="000001.SZ", side="BUY",
        trade_date="2026-07-22", price=10.5, quantity=1000,
        commission=5, tax=0, client_execution_id="exec-1",
    )
    e2 = repo.create_execution(
        account_id=acc["id"], stock_code="000001.SZ", side="BUY",
        trade_date="2026-07-22", price=10.5, quantity=1000,
        commission=5, tax=0, client_execution_id="exec-1",
    )
    assert e1["id"] == e2["id"]
    assert e2["reused"] is True


def test_list_executions(repo):
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    repo.create_execution(account_id=acc["id"], stock_code="000001.SZ", side="BUY",
                          trade_date="2026-07-22", price=10, quantity=100,
                          commission=5, tax=0, client_execution_id="e1")
    repo.create_execution(account_id=acc["id"], stock_code="000001.SZ", side="SELL",
                          trade_date="2026-07-23", price=11, quantity=100,
                          commission=5, tax=1, client_execution_id="e2")
    execs = repo.list_executions(acc["id"], start="2026-07-22", end="2026-07-23")
    assert len(execs) == 2


# ---- Equity Snapshot ----

def test_upsert_equity_snapshot(repo):
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    repo.upsert_equity_snapshot(
        account_id=acc["id"], trade_date="2026-07-22",
        cash=90000, market_value=15000, total_equity=105000,
        exposure=0.1428, peak_equity=105000, drawdown=0,
    )
    snap = repo.get_equity_snapshot(acc["id"], "2026-07-22")
    assert snap["total_equity"] == 105000


def test_get_latest_equity_snapshot(repo):
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    repo.upsert_equity_snapshot(account_id=acc["id"], trade_date="2026-07-21",
                                cash=100000, market_value=0, total_equity=100000,
                                exposure=0, peak_equity=100000, drawdown=0)
    repo.upsert_equity_snapshot(account_id=acc["id"], trade_date="2026-07-22",
                                cash=95000, market_value=8000, total_equity=103000,
                                exposure=0.0776, peak_equity=103000, drawdown=0)
    latest = repo.get_latest_equity_snapshot(acc["id"])
    assert latest["trade_date"] == "2026-07-22"


def test_get_latest_equity_snapshot_none(repo):
    acc = repo.create_account(name="main", initial_equity=100000, cash_balance=100000)
    assert repo.get_latest_equity_snapshot(acc["id"]) is None


# ---- Audit Log ----

def test_write_audit_log(repo):
    audit_id = repo.write_audit_log(
        actor="user", action="CREATE_ACCOUNT",
        entity_type="account", entity_id="1",
        after_json={"name": "main"},
        request_id="req-1",
    )
    assert audit_id > 0
