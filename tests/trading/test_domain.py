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
    ("000300.SH", "000300.SH"),
    ("000905.SH", "000905.SH"),
])
def test_normalize_stock_code(raw, expected):
    assert normalize_stock_code(raw) == expected


def test_bare_0009_stock_is_shenzhen():
    assert normalize_stock_code("000936") == "000936.SZ"


def test_known_bare_index_requires_index_context():
    assert normalize_stock_code("000905", kind="index") == "000905.SH"
    assert normalize_stock_code("000905", kind="stock") == "000905.SZ"


def test_explicit_market_is_preserved():
    assert normalize_stock_code("000905.SH", kind="index") == "000905.SH"


def test_bare_code():
    assert bare_code("000001.SZ") == "000001"


def test_market_prefix():
    assert market_prefix("000001.SZ") == "0"
    assert market_prefix("600000.SH") == "1"


def test_normalize_invalid():
    with pytest.raises(ValueError):
        normalize_stock_code("abc")
