from datetime import date
import pytest

from backend.trading.providers.akshare_provider import AkshareProvider
from backend.trading.providers.base import ProviderError
from tests.trading.test_provider_contract import ProviderContractTest


def _make_daily_df(symbol=None, start_date=None, end_date=None):
    """构造一个类 DataFrame 对象(用 list of dict 模拟,避免真实 pandas 依赖)。

    akshare 的 stock_zh_a_hist 返回中文列名:日期/开盘/收盘/最高/最低/成交量/成交额/涨跌幅
    """
    class FakeDF:
        def __init__(self, rows):
            self._rows = rows
            self.empty = len(rows) == 0
        def iterrows(self):
            for r in self._rows:
                yield 0, r

    rows = [
        {"日期": "2026-07-14", "开盘": 10.50, "收盘": 10.55, "最高": 10.60, "最低": 10.45, "成交量": 1500000, "成交额": 16000000.00, "涨跌幅": -0.95},
        {"日期": "2026-07-15", "开盘": 10.55, "收盘": 10.50, "最高": 10.60, "最低": 10.44, "成交量": 1200000, "成交额": 13000000.00, "涨跌幅": -0.47},
        {"日期": "2026-07-16", "开盘": 10.50, "收盘": 10.60, "最高": 10.65, "最低": 10.48, "成交量": 1800000, "成交额": 19000000.00, "涨跌幅": 1.14},
    ]
    return FakeDF(rows)


def _make_empty_df(symbol=None, start_date=None, end_date=None):
    class FakeDF:
        empty = True
        def iterrows(self):
            return iter([])
    return FakeDF()


class _FakeAkModule:
    """模拟 akshare 模块(避免真实安装)。"""

    def __init__(self, daily_factory):
        self._daily_factory = daily_factory
        self.__version__ = "1.12.0"

    def stock_zh_a_hist(self, symbol, period, start_date, end_date, adjust):
        return self._daily_factory(symbol=symbol, start_date=start_date, end_date=end_date)


class TestAkshareContract(ProviderContractTest):
    """Akshare 必须通过 Provider 契约测试。

    make_provider 注入 FakeAkModule,查询 999999 时返回空 df(模拟"成功但无数据")。
    """

    def make_provider(self, monkeypatch):
        provider = AkshareProvider()
        # 默认 factory 返回正常 df;对 999999 返回空
        def factory(symbol=None, start_date=None, end_date=None):
            if symbol and "999999" in str(symbol):
                return _make_empty_df()
            return _make_daily_df()
        fake_ak = _FakeAkModule(factory)
        monkeypatch.setattr(provider, "_ak", fake_ak)
        return provider


def test_get_daily_bars_normal():
    provider = AkshareProvider()
    provider._ak = _FakeAkModule(_make_daily_df)
    bars = provider.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 16))
    assert len(bars) == 3
    assert bars[0].code == "000001.SZ"
    assert bars[0].open == 10.50
    assert bars[1].trade_date == date(2026, 7, 15)


def test_get_daily_bars_ak_not_installed(monkeypatch):
    provider = AkshareProvider()
    monkeypatch.setattr(provider, "_ak", None)
    with pytest.raises(ProviderError) as exc:
        provider.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 16))
    assert "akshare" in str(exc.value).lower() or "未" in str(exc.value)


def test_get_daily_bars_ak_call_exception():
    provider = AkshareProvider()

    class BoomAk:
        __version__ = "1.12.0"
        def stock_zh_a_hist(self, **kw):
            raise RuntimeError("api rate limit")

    provider._ak = BoomAk()
    with pytest.raises(ProviderError):
        provider.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 16))


def test_get_daily_bars_skips_invalid_ohlc():
    """OHLC 不合法的行应被跳过,不进入结果。"""
    provider = AkshareProvider()

    class BadRowAk:
        __version__ = "1.12.0"
        def stock_zh_a_hist(self, **kw):
            class FakeDF:
                empty = False
                def iterrows(self):
                    # low > high 的非法行
                    yield 0, {"日期": "2026-07-14", "开盘": 10.0, "收盘": 10.0,
                              "最高": 9.0, "最低": 11.0, "成交量": 100, "成交额": 1000.0, "涨跌幅": 0.0}
            return FakeDF()

    provider._ak = BadRowAk()
    bars = provider.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 16))
    assert bars == []


def test_akshare_calendar_failure_does_not_fallback_to_weekdays():
    provider = AkshareProvider()

    class BrokenAk:
        @staticmethod
        def tool_trade_date_hist_sina():
            raise RuntimeError("calendar offline")

    provider._ak = BrokenAk()
    with pytest.raises(ProviderError) as exc:
        provider.get_trade_calendar(date(2026, 10, 1), date(2026, 10, 1))
    assert exc.value.retriable is True


def test_akshare_calendar_requires_installed_provider(monkeypatch):
    provider = AkshareProvider()
    monkeypatch.setattr(provider, "_ak", None)

    with pytest.raises(ProviderError) as exc:
        provider.get_trade_calendar(date(2026, 10, 1), date(2026, 10, 1))
    assert exc.value.retriable is False


def test_akshare_empty_calendar_fails_closed():
    provider = AkshareProvider()

    class EmptyCalendarAk:
        @staticmethod
        def tool_trade_date_hist_sina():
            return _make_empty_df()

    provider._ak = EmptyCalendarAk()
    with pytest.raises(ProviderError, match="交易日历"):
        provider.get_trade_calendar(date(2026, 10, 1), date(2026, 10, 2))


def test_akshare_calendar_must_cover_requested_horizon():
    provider = AkshareProvider()

    class StaleCalendarAk:
        @staticmethod
        def tool_trade_date_hist_sina():
            class FakeDF:
                def iterrows(self):
                    yield 0, {"trade_date": "2026-09-30"}

            return FakeDF()

    provider._ak = StaleCalendarAk()
    with pytest.raises(ProviderError, match="交易日历"):
        provider.get_trade_calendar(date(2026, 10, 1), date(2026, 10, 2))
