import os
from datetime import date
import pytest
from backend.trading.errors import TradingError
from backend.trading.services.execution_service import ExecutionService
from backend.trading.repository import TradingRepository
from backend.trading.migrations import run_migrations

TEST_DB = "data/test_exec_svc.db"


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


def test_buy_execution_updates_cash_and_position(setup):
    """BUY:现金减少,持仓增加,available_quantity 当日=0(T+1)。"""
    repo, acc = setup
    svc = ExecutionService(repo)
    result = svc.record_execution(
        account_id=acc["id"], stock_code="000001.SZ", side="BUY",
        trade_date="2026-07-22", price=10.0, quantity=1000,
        commission=5.0, tax=0, client_execution_id="exec-1",
    )
    assert result["execution"]["reused"] is False
    # 现金:100000 - 1000*10 - 5 = 89995
    acc_after = repo.get_account(acc["id"])
    assert acc_after["cash_balance"] == 89995
    # 持仓
    pos = repo.get_position(acc["id"], "000001.SZ")
    assert pos["quantity"] == 1000
    assert pos["available_quantity"] == 0  # T+1
    assert pos["average_cost"] == 10.0


def test_sell_execution_updates_cash_and_position(setup):
    """SELL:现金增加,持仓减少。"""
    repo, acc = setup
    # 先买入并 T+1 滚动
    repo.upsert_position(account_id=acc["id"], stock_code="000001.SZ", quantity=1000,
                         available_quantity=1000, average_cost=10)
    svc = ExecutionService(repo)
    svc.record_execution(
        account_id=acc["id"], stock_code="000001.SZ", side="SELL",
        trade_date="2026-07-22", price=12.0, quantity=500,
        commission=5.0, tax=5.0, client_execution_id="sell-1",
    )
    # 现金:100000 + 500*12 - 5 - 5 = 105990
    acc_after = repo.get_account(acc["id"])
    assert acc_after["cash_balance"] == 105990
    pos = repo.get_position(acc["id"], "000001.SZ")
    assert pos["quantity"] == 500
    assert pos["available_quantity"] == 500


def test_buy_blends_average_cost(setup):
    """多次 BUY 平均成本混合。"""
    repo, acc = setup
    svc = ExecutionService(repo)
    svc.record_execution(
        account_id=acc["id"], stock_code="000001.SZ", side="BUY",
        trade_date="2026-07-22", price=10.0, quantity=1000,
        commission=0, tax=0, client_execution_id="b1",
    )
    # T+1 滚动 available
    svc.roll_t1_available(acc["id"], "2026-07-23")
    svc.record_execution(
        account_id=acc["id"], stock_code="000001.SZ", side="BUY",
        trade_date="2026-07-23", price=12.0, quantity=1000,
        commission=0, tax=0, client_execution_id="b2",
    )
    pos = repo.get_position(acc["id"], "000001.SZ")
    # avg = (1000*10 + 1000*12) / 2000 = 11
    assert pos["quantity"] == 2000
    assert pos["average_cost"] == pytest.approx(11.0)


def test_idempotent_on_client_execution_id(setup):
    """相同 client_execution_id 幂等,不重复扣现金。"""
    repo, acc = setup
    svc = ExecutionService(repo)
    r1 = svc.record_execution(
        account_id=acc["id"], stock_code="000001.SZ", side="BUY",
        trade_date="2026-07-22", price=10.0, quantity=1000,
        commission=0, tax=0, client_execution_id="dup-1",
    )
    r2 = svc.record_execution(
        account_id=acc["id"], stock_code="000001.SZ", side="BUY",
        trade_date="2026-07-22", price=10.0, quantity=1000,
        commission=0, tax=0, client_execution_id="dup-1",
    )
    assert r1["execution"]["id"] == r2["execution"]["id"]
    assert r2["execution"]["reused"] is True
    # 现金只扣一次
    assert repo.get_account(acc["id"])["cash_balance"] == 100000 - 10000


@pytest.mark.parametrize(
    ("field", "different_value"),
    [
        ("account_id", "other-account"),
        ("stock_code", "600000.SH"),
        ("side", "SELL"),
        ("trade_date", "2026-07-23"),
        ("price", 10.5),
        ("quantity", 1100),
        ("commission", 6.0),
        ("tax", 2.0),
        ("note", "changed"),
        ("plan_item_id", 999),
    ],
)
def test_client_execution_id_conflict_rejects_changed_payload_without_writes(
    setup, field, different_value
):
    repo, acc = setup
    other = repo.create_account(
        name="secondary",
        initial_equity=50_000,
        cash_balance=50_000,
        is_active=False,
    )
    svc = ExecutionService(repo)
    original = {
        "account_id": acc["id"],
        "stock_code": "000001.SZ",
        "side": "BUY",
        "trade_date": "2026-07-22",
        "price": 10.0,
        "quantity": 1000,
        "commission": 5.0,
        "tax": 1.0,
        "note": "original",
        "plan_item_id": None,
        "client_execution_id": "payload-bound",
    }
    svc.record_execution(**original)
    before = {
        "account": repo.get_account(acc["id"]),
        "other": repo.get_account(other["id"]),
        "position": repo.get_position(acc["id"], "000001.SZ"),
        "executions": repo.list_executions(acc["id"]),
        "audits": repo.list_audit_logs(action="EXECUTION_BUY"),
    }
    replay = dict(original)
    replay[field] = (
        other["id"] if different_value == "other-account" else different_value
    )

    with pytest.raises(TradingError) as exc_info:
        svc.record_execution(**replay)

    assert exc_info.value.code == "EXECUTION_IDEMPOTENCY_CONFLICT"
    assert field in exc_info.value.details["conflicting_fields"]
    assert repo.get_account(acc["id"]) == before["account"]
    assert repo.get_account(other["id"]) == before["other"]
    assert repo.get_position(acc["id"], "000001.SZ") == before["position"]
    assert repo.list_executions(acc["id"]) == before["executions"]
    assert repo.list_audit_logs(action="EXECUTION_BUY") == before["audits"]


def test_sell_exceeds_available_raises(setup):
    """卖出超过可卖数量 -> 拒绝。"""
    repo, acc = setup
    repo.upsert_position(account_id=acc["id"], stock_code="000001.SZ", quantity=1000,
                         available_quantity=500, average_cost=10)
    svc = ExecutionService(repo)
    with pytest.raises(Exception, match="available|可卖"):
        svc.record_execution(
            account_id=acc["id"], stock_code="000001.SZ", side="SELL",
            trade_date="2026-07-22", price=12.0, quantity=600,
            commission=0, tax=0, client_execution_id="s1",
        )


def test_sell_nonexistent_position_raises(setup):
    """卖出无持仓 -> 拒绝。"""
    repo, acc = setup
    svc = ExecutionService(repo)
    with pytest.raises(Exception, match="持仓"):
        svc.record_execution(
            account_id=acc["id"], stock_code="999999.SZ", side="SELL",
            trade_date="2026-07-22", price=12.0, quantity=100,
            commission=0, tax=0, client_execution_id="s2",
        )


def test_buy_insufficient_cash_raises(setup):
    """现金不足 -> 拒绝。"""
    repo, acc = setup
    svc = ExecutionService(repo)
    with pytest.raises(Exception, match="现金|cash"):
        svc.record_execution(
            account_id=acc["id"], stock_code="000001.SZ", side="BUY",
            trade_date="2026-07-22", price=1000.0, quantity=1000,
            commission=0, tax=0, client_execution_id="b3",
        )


def test_roll_t1_available(setup):
    """T+1 滚动:available_quantity 变为 quantity。"""
    repo, acc = setup
    svc = ExecutionService(repo)
    svc.record_execution(
        account_id=acc["id"], stock_code="000001.SZ", side="BUY",
        trade_date="2026-07-22", price=10.0, quantity=1000,
        commission=0, tax=0, client_execution_id="t1",
    )
    # 买入当日 available=0
    pos = repo.get_position(acc["id"], "000001.SZ")
    assert pos["available_quantity"] == 0
    # 滚动到下一交易日
    svc.roll_t1_available(acc["id"], "2026-07-23")
    pos = repo.get_position(acc["id"], "000001.SZ")
    assert pos["available_quantity"] == 1000


def test_audit_log_written(setup):
    """每次成交写审计日志。"""
    repo, acc = setup
    svc = ExecutionService(repo)
    svc.record_execution(
        account_id=acc["id"], stock_code="000001.SZ", side="BUY",
        trade_date="2026-07-22", price=10.0, quantity=100,
        commission=0, tax=0, client_execution_id="audit-1",
    )
    execs = repo.list_executions(acc["id"])
    assert len(execs) == 1
