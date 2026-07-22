import os
import pytest
from backend.trading.services.account_service import AccountService
from backend.trading.repository import TradingRepository
from backend.trading.migrations import run_migrations

TEST_DB = "data/test_account_svc.db"


@pytest.fixture
def service():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    yield AccountService(TradingRepository(TEST_DB))
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


def test_create_account(service):
    acc = service.create_account(name="main", initial_equity=100000, cash_balance=100000)
    assert acc["id"] > 0


def test_get_account(service):
    created = service.create_account(name="main", initial_equity=100000, cash_balance=100000)
    fetched = service.get_account(created["id"])
    assert fetched["name"] == "main"


def test_list_accounts(service):
    service.create_account(name="a", initial_equity=100000, cash_balance=100000)
    service.create_account(name="b", initial_equity=50000, cash_balance=50000)
    assert len(service.list_accounts()) == 2


def test_update_account(service):
    acc = service.create_account(name="main", initial_equity=100000, cash_balance=100000)
    service.update_account(acc["id"], {"cash_balance": 80000})
    assert service.get_account(acc["id"])["cash_balance"] == 80000


def test_update_account_rejects_initial_equity_change(service):
    """initial_equity 创建后不可改(语义约束)。"""
    acc = service.create_account(name="main", initial_equity=100000, cash_balance=100000)
    with pytest.raises(ValueError, match="initial_equity"):
        service.update_account(acc["id"], {"initial_equity": 999999})


def test_set_active_account_deactivates_others(service):
    """设某账户 active 时,其他账户自动 inactive(单主账户约束,spec §3.2)。"""
    a1 = service.create_account(name="a", initial_equity=100000, cash_balance=100000, is_active=True)
    a2 = service.create_account(name="b", initial_equity=50000, cash_balance=50000, is_active=True)
    # a2 设为 active 后,a1 应自动 inactive
    fetched_a1 = service.get_account(a1["id"])
    fetched_a2 = service.get_account(a2["id"])
    # 创建时都 active,但 service 应保证只有一个 active
    actives = [a for a in service.list_accounts() if a["is_active"]]
    assert len(actives) == 1


def test_activate_account(service):
    acc = service.create_account(name="main", initial_equity=100000, cash_balance=100000, is_active=False)
    service.activate_account(acc["id"])
    assert service.get_account(acc["id"])["is_active"] is True
