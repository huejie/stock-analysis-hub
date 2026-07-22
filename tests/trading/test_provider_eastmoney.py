import json
from datetime import date
from unittest.mock import patch
import pytest

from backend.trading.providers.eastmoney import EastmoneyProvider
from backend.trading.providers.base import ProviderError
from tests.trading.test_provider_contract import ProviderContractTest, load_fixture


def _mock_response(payload: dict):
    """_http_get 契约:返回已解析的 JSON dict(见 eastmoney._http_get 的 -> dict)。"""
    return payload


class TestEastmoneyContract(ProviderContractTest):
    """Eastmoney 必须通过 Provider 契约测试。

    make_provider 在被查询 999999.SZ 时返回 empty_response.json(模拟"成功但无数据")。
    """

    def make_provider(self, monkeypatch):
        provider = EastmoneyProvider()

        def fake_get(url, **kwargs):
            # 查询 999999 时返回空响应
            if "999999" in str(kwargs.get("params", "")):
                return _mock_response(load_fixture("empty_response.json"))
            return _mock_response(load_fixture("daily_bars_em.json"))
        monkeypatch.setattr(provider, "_http_get", fake_get)
        return provider


def test_parse_kline_line():
    """解析一行 kline 字符串(逗号分隔形式,内部转换用)。"""
    provider = EastmoneyProvider()
    bar = provider._parse_kline_line(
        "000001.SZ", "2026-07-14,10.50,10.55,10.60,10.45,1500000,16000000.00,1.0,-0.95"
    )
    assert bar is not None
    assert bar.code == "000001.SZ"
    assert bar.trade_date == date(2026, 7, 14)
    assert bar.open == 10.50
    assert bar.close == 10.55
    assert bar.high == 10.60
    assert bar.low == 10.45
    assert bar.volume == 1500000
    assert bar.amount == 16000000.00
    assert bar.adjust_factor == 1.0


def test_get_daily_bars_filters_date_range(monkeypatch):
    provider = EastmoneyProvider()
    monkeypatch.setattr(provider, "_http_get",
                        lambda *a, **k: _mock_response(load_fixture("daily_bars_em.json")))
    bars = provider.get_daily_bars(["000001.SZ"], date(2026, 7, 15), date(2026, 7, 17))
    dates = [b.trade_date for b in bars]
    assert dates == [date(2026, 7, 15), date(2026, 7, 16), date(2026, 7, 17)]


def test_get_daily_bars_http_error_raises(monkeypatch):
    import httpx
    provider = EastmoneyProvider()

    def boom(*a, **k):
        raise httpx.ConnectError("network down")
    monkeypatch.setattr(provider, "_http_get", boom)
    with pytest.raises(ProviderError) as exc:
        provider.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20))
    assert exc.value.retriable is True


def test_secid_for_sh():
    provider = EastmoneyProvider()
    assert provider._secid("600000.SH") == "1.600000"
    assert provider._secid("000001.SZ") == "0.000001"
    assert provider._secid("000300.SH") == "1.000300"


def test_empty_response_returns_empty_list(monkeypatch):
    """空 klines 返回空列表,不抛异常。"""
    provider = EastmoneyProvider()
    monkeypatch.setattr(provider, "_http_get",
                        lambda *a, **k: _mock_response(load_fixture("empty_response.json")))
    bars = provider.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20))
    assert bars == []
