from datetime import date
import pytest

from backend.trading.providers.eastmoney import EastmoneyProvider
from backend.trading.providers.base import ProviderError
from tests.trading.test_provider_contract import ProviderContractTest, load_fixture


def _mock_response(payload: dict, symbol: str = "sz000001"):
    """_http_get 契约:返回已解析的 JSON dict(见 eastmoney._http_get 的 -> dict)。"""
    payload.setdefault("code", 0)
    payload.setdefault("msg", "")
    data = payload.get("data")
    if isinstance(data, dict):
        bare_key = next(
            (key for key in data if len(key) == 6 and key.isdigit()), None
        )
        if bare_key is not None:
            data[symbol] = data.pop(bare_key)
    return payload


def _daily_bars_fixture_for(symbol: str) -> dict:
    payload = load_fixture("daily_bars_em.json")
    return _mock_response(payload, symbol)


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


def test_get_daily_bars_resolves_bare_0009_code_as_stock(monkeypatch):
    provider = EastmoneyProvider()
    requested_params = []

    def fake_get(*args, **kwargs):
        requested_params.append(kwargs["params"]["param"])
        return _mock_response(_daily_bars_fixture_for("sz000936"))

    monkeypatch.setattr(provider, "_http_get", fake_get)
    bars = provider.get_daily_bars(["000936"], date(2026, 7, 14), date(2026, 7, 20))

    assert requested_params[0].startswith("sz000936,")
    assert {bar.code for bar in bars} == {"000936.SZ"}


def test_get_index_bars_resolves_known_bare_index(monkeypatch):
    provider = EastmoneyProvider()
    requested_params = []

    def fake_get(*args, **kwargs):
        requested_params.append(kwargs["params"]["param"])
        return _mock_response(_daily_bars_fixture_for("sh000905"))

    monkeypatch.setattr(provider, "_http_get", fake_get)
    bars = provider.get_index_bars(["000905"], date(2026, 7, 14), date(2026, 7, 20))

    assert requested_params[0].startswith("sh000905,")
    assert {bar.code for bar in bars} == {"000905.SH"}


def test_get_daily_bars_http_error_raises(monkeypatch):
    import httpx
    provider = EastmoneyProvider()

    def boom(*a, **k):
        raise httpx.ConnectError("network down")
    monkeypatch.setattr(provider, "_http_get", boom)
    with pytest.raises(ProviderError) as exc:
        provider.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20))
    assert exc.value.retriable is True


def test_tencent_symbol_uses_exchange_prefix():
    provider = EastmoneyProvider()
    assert provider._tencent_symbol("000001.SZ") == "sz000001"
    assert provider._tencent_symbol("600000.SH") == "sh600000"
    assert provider._tencent_symbol("000300.SH") == "sh000300"
    assert provider._tencent_symbol("920000.BJ") == "bj920000"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"code": 1, "msg": "", "data": {}},
        {"code": 0, "msg": "", "data": []},
        {"code": 0, "msg": "", "data": {"sz000001": []}},
        {
            "code": 0,
            "msg": "",
            "data": {"sz000001": {"qfqday": "not-a-list"}},
        },
        {"code": 0, "msg": "", "data": {"sz000001": {"qfqday": {}}}},
        {"code": 0, "msg": "", "data": {"sz000001": {"qfqday": ""}}},
        {"code": 0, "msg": "", "data": {"sz000001": {"qfqday": 0}}},
        {
            "code": 0,
            "msg": "",
            "data": {"sz000001": {"qfqday": [{"date": "2026-07-14"}]}},
        },
    ],
    ids=[
        "non-object",
        "error-code",
        "non-object-data",
        "non-object-node",
        "non-list-bars",
        "falsey-object-bars",
        "falsey-string-bars",
        "falsey-number-bars",
        "non-row-bar",
    ],
)
def test_tencent_protocol_error_raises_provider_error(monkeypatch, payload):
    provider = EastmoneyProvider()
    monkeypatch.setattr(provider, "_http_get", lambda *args, **kwargs: payload)

    with pytest.raises(ProviderError, match="协议错误"):
        provider.get_daily_bars(
            ["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20)
        )


def test_empty_response_returns_empty_list(monkeypatch):
    """空 klines 返回空列表,不抛异常。"""
    provider = EastmoneyProvider()
    monkeypatch.setattr(provider, "_http_get",
                        lambda *a, **k: _mock_response(load_fixture("empty_response.json")))
    bars = provider.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20))
    assert bars == []


def test_eastmoney_calendar_is_not_faked_from_weekdays():
    provider = EastmoneyProvider()
    with pytest.raises(ProviderError) as exc:
        provider.get_trade_calendar(date(2026, 10, 1), date(2026, 10, 1))
    assert exc.value.retriable is False
