import pytest
from pydantic import ValidationError
from backend.trading.schemas import (
    StockPoolImportRequest,
    StockPoolVersionResponse,
    StockPoolItemResponse,
    DataJobCreateRequest,
    DataJobResponse,
    DataHealthResponse,
    DataIssueOut,
)


def test_stock_pool_import_text():
    req = StockPoolImportRequest(
        pool_name="default",
        source="text",
        text_body="000001 平安银行\n600000 浦发银行\n300750",
    )
    assert len(req.parsed_items()) == 3
    assert req.parsed_items()[0]["stock_code"] == "000001.SZ"
    assert req.parsed_items()[2]["stock_code"] == "300750.SZ"


def test_stock_pool_import_csv():
    csv = "code,name\n000001,平安\n600000,浦发"
    req = StockPoolImportRequest(pool_name="default", source="csv", text_body=csv)
    items = req.parsed_items()
    assert items[0]["stock_name"] == "平安"
    assert items[1]["stock_code"] == "600000.SH"


def test_stock_pool_import_requires_content():
    with pytest.raises(ValidationError):
        StockPoolImportRequest(pool_name="default", source="text", text_body="")


def test_data_job_create_validates_type():
    req = DataJobCreateRequest(job_type="update_bars", trade_date="2026-07-21")
    assert req.job_type == "update_bars"


def test_data_job_create_rejects_unknown_type():
    with pytest.raises(ValidationError):
        DataJobCreateRequest(job_type="bad_type", trade_date="2026-07-21")
