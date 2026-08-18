from backend.trading.migrations import run_migrations
from backend.trading.repository import TradingRepository


def _repository(tmp_path) -> TradingRepository:
    db_path = str(tmp_path / "runtime.db")
    run_migrations(db_path)
    return TradingRepository(db_path)


def test_stock_pool_versions_default_to_compatible_list_and_support_usable_filter(
    tmp_path,
):
    repo = _repository(tmp_path)
    created = repo.create_stock_pool_version(
        "default",
        [{"stock_code": "000001.SZ", "stock_name": "平安银行"}],
    )

    assert repo.list_stock_pool_versions("default", usable_only=True)[0]["id"] == created["id"]
    assert repo.mark_stock_pool_unusable(created["id"], reason="instrument mismatch") is True
    assert repo.mark_stock_pool_unusable(created["id"], reason="duplicate") is False
    assert repo.mark_stock_pool_unusable(999_999, reason="missing") is False
    assert repo.list_stock_pool_versions("default", usable_only=True) == []

    compatible = repo.list_stock_pool_versions("default")
    assert compatible[0]["id"] == created["id"]
    assert compatible[0]["is_usable"] == 0
    assert compatible[0]["invalid_reason"] == "instrument mismatch"


def test_plan_item_execution_status_uses_compare_and_swap(tmp_path):
    repo = _repository(tmp_path)
    item = repo.create_plan_item(
        plan_run_id=1,
        stock_code="000001.SZ",
        action="WATCH",
    )

    assert repo.update_plan_item_execution_status(
        item["id"], "PENDING", "NOT_FILLED"
    ) is True
    assert repo.update_plan_item_execution_status(
        item["id"], "PENDING", "NOT_FILLED"
    ) is False
    assert repo.get_plan_items(1)[0]["execution_status"] == "NOT_FILLED"
