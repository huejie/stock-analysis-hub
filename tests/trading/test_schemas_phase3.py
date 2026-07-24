import pytest
from pydantic import ValidationError
from backend.trading.schemas import (
    StrategyCreateRequest, StrategyResponse, StrategyActivateResponse,
    PlanRunCreateRequest, PlanRunResponse, PlanRunDetailResponse,
    PlanItemResponse, PlanPublishResponse,
)


def test_strategy_create():
    req = StrategyCreateRequest(
        strategy_code="default", name="v1",
        params_json={"risk_per_trade": 0.005, "min_score": 70},
    )
    assert req.strategy_code == "default"
    assert req.params_json["min_score"] == 70


def test_strategy_create_rejects_empty_code():
    with pytest.raises(ValidationError):
        StrategyCreateRequest(strategy_code="", name="v1", params_json={})


def test_plan_run_create():
    req = PlanRunCreateRequest(
        account_id=1, signal_date="2026-07-22",
        stock_pool_version_id=1, strategy_version_id=1,
    )
    assert req.force_new_version is False  # 默认


def test_plan_run_create_force_new():
    req = PlanRunCreateRequest(
        account_id=1, signal_date="2026-07-22",
        stock_pool_version_id=1, strategy_version_id=1,
        force_new_version=True,
    )
    assert req.force_new_version is True


def test_plan_item_response_defaults():
    """PlanItemResponse 必填只有 id/stock_code/action,其余有默认值。"""
    item = PlanItemResponse(id=1, stock_code="000001.SZ", action="WATCH")
    assert item.suggested_quantity == 0
    assert item.execution_status == "PENDING"
    assert item.rule_hits == []
