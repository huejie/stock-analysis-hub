import pytest
from backend.trading.domain import normalize_stock_code, bare_code, market_prefix


@pytest.mark.parametrize("raw,expected", [
    ("000001", "000001.SZ"),
    ("sz000001", "000001.SZ"),
    ("SZ000001", "000001.SZ"),
    ("000001.SZ", "000001.SZ"),
    ("600000", "600000.SH"),
    ("sh600000", "600000.SH"),
    ("300750", "300750.SZ"),
    ("688981", "688981.SH"),
    ("000300", "000300.SH"),   # 沪深300 指数
    ("000905", "000905.SH"),   # 中证500 指数
])
def test_normalize_stock_code(raw, expected):
    assert normalize_stock_code(raw) == expected


def test_bare_code():
    assert bare_code("000001.SZ") == "000001"


def test_market_prefix():
    assert market_prefix("000001.SZ") == "0"
    assert market_prefix("600000.SH") == "1"


def test_normalize_invalid():
    with pytest.raises(ValueError):
        normalize_stock_code("abc")
