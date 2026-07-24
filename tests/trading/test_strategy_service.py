import os
import pytest
from backend.trading.repository import TradingRepository
from backend.trading.migrations import run_migrations
from backend.trading.services.strategy_service import StrategyService

TEST_DB = "data/test_strategy_service.db"


@pytest.fixture
def service():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    repo = TradingRepository(TEST_DB)
    yield StrategyService(repo)
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


def test_create_strategy_returns_draft(service):
    out = service.create_strategy(
        strategy_code="default", name="v1",
        params_json={"risk_per_trade": 0.005, "min_score": 70},
    )
    assert out["status"] == "DRAFT"
    assert out["version_no"] == 1
    assert out["reused"] is False


def test_create_strategy_idempotent_on_params(service):
    p1 = {"risk_per_trade": 0.005, "min_score": 70}
    p2 = {"min_score": 70, "risk_per_trade": 0.005}  # 键顺序不同,同 hash
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json=p1)
    v2 = service.create_strategy(strategy_code="default", name="v1-dup", params_json=p2)
    assert v1["id"] == v2["id"]
    assert v2["reused"] is True


def test_activate_strategy_stub_warning_and_retire_previous(service):
    """激活 DRAFT→ACTIVE,旧 ACTIVE→RETIRED,返回 stub 门禁 warning。"""
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    v2 = service.create_strategy(strategy_code="default", name="v2", params_json={"a": 2})
    # 激活 v1
    r1 = service.activate_strategy(v1["id"])
    assert r1["status"] == "ACTIVE"
    assert r1["retired_previous_id"] is None
    # stub 门禁 warning
    assert any("门禁" in w for w in r1["warnings"])
    # 激活 v2,v1 应变 RETIRED
    r2 = service.activate_strategy(v2["id"])
    assert r2["retired_previous_id"] == v1["id"]
    assert service.get_strategy(v1["id"])["status"] == "RETIRED"
    assert service.get_strategy(v2["id"])["status"] == "ACTIVE"


def test_get_active_strategy(service):
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    service.activate_strategy(v1["id"])
    active = service.get_active_strategy("default")
    assert active is not None
    assert active["id"] == v1["id"]


def test_get_active_strategy_none(service):
    service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    assert service.get_active_strategy("default") is None


def test_list_strategies(service):
    service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    service.create_strategy(strategy_code="default", name="v2", params_json={"a": 2})
    assert len(service.list_strategies("default")) == 2
    assert len(service.list_strategies()) == 2


def test_activate_strategy_nonexistent_raises(service):
    with pytest.raises(ValueError, match="不存在"):
        service.activate_strategy(99999)


def test_activate_strategy_retired_raises(service):
    """已 RETIRED 的策略不能再激活。"""
    v1 = service.create_strategy(strategy_code="default", name="v1", params_json={"a": 1})
    v2 = service.create_strategy(strategy_code="default", name="v2", params_json={"a": 2})
    service.activate_strategy(v1["id"])
    service.activate_strategy(v2["id"])  # v1 → RETIRED
    with pytest.raises(ValueError, match="退役"):
        service.activate_strategy(v1["id"])
