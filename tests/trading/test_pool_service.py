import os
import pytest
from backend.trading.services.pool_service import PoolService
from backend.trading.repository import TradingRepository
from backend.trading.migrations import run_migrations

TEST_DB = "data/test_pool_service.db"


@pytest.fixture
def service():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    run_migrations(TEST_DB)
    repo = TradingRepository(TEST_DB)
    yield PoolService(repo)
    import gc; gc.collect()
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


def test_import_from_text(service):
    result = service.import_from_text(
        "default", "000001 平安银行\n600000 浦发银行\n300750"
    )
    assert result["version_no"] == 1
    assert result["items_count"] == 3


def test_import_dedup_codes(service):
    result = service.import_from_text(
        "default", "000001\n000001\n000001.SZ\nsz000001"
    )
    assert result["items_count"] == 1


def test_import_idempotent(service):
    r1 = service.import_from_text("default", "000001\n600000")
    r2 = service.import_from_text("default", "000001\n600000")
    assert r1["version_no"] == r2["version_no"]
    assert r2["reused"] is True


def test_import_creates_new_version_on_change(service):
    r1 = service.import_from_text("default", "000001")
    r2 = service.import_from_text("default", "000001\n600000")
    assert r2["version_no"] == r1["version_no"] + 1


def test_get_version_with_items(service):
    r = service.import_from_text("default", "000001 平安")
    v = service.get_version(r["id"])
    assert v["items"][0]["stock_name"] == "平安"


def test_list_versions(service):
    service.import_from_text("default", "000001")
    service.import_from_text("default", "000001\n600000")
    versions = service.list_versions("default")
    assert len(versions) == 2
    assert versions[0]["version_no"] == 2


def test_import_empty_raises(service):
    """解析后无有效代码应抛 ValueError。"""
    with pytest.raises(ValueError):
        service.import_from_text("default", "no valid codes here")
