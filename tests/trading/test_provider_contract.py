"""所有 Provider 实现必须通过此模块定义的契约测试。

子类化 ProviderContractTest 并实现 make_provider() 返回一个 Provider 实例,
并通过 monkeypatch 拦截其网络调用(返回 fixture)。

文档第 15.2 章:返回统一代码与字段单位、处理空响应、OHLC 校验、不重复记录。
"""
import json
from datetime import date
from pathlib import Path
import pytest

from backend.trading.domain import DailyBar
from backend.trading.providers.base import MarketDataProvider, ProviderError

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "trading"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class ProviderContractTest:
    """子类必须实现 make_provider() 返回一个 Provider 实例,
    并通过 monkeypatch 拦截其网络调用(返回 fixture)。
    """

    provider: MarketDataProvider

    def make_provider(self, monkeypatch) -> MarketDataProvider:
        raise NotImplementedError

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        self.provider = self.make_provider(monkeypatch)

    def test_returns_normalized_codes(self):
        """返回的 DailyBar.code 必须是 000001.SZ 形式。"""
        bars = self.provider.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20))
        assert len(bars) > 0
        for b in bars:
            assert b.code.endswith(".SZ") or b.code.endswith(".SH")
            assert len(b.code) == 9  # 6位.2位

    def test_ohlc_validity(self):
        """OHLC 必须满足 low <= open/close <= high,价格为正。"""
        bars = self.provider.get_daily_bars(["000001.SZ"], date(2026, 7, 14), date(2026, 7, 20))
        for b in bars:
            assert b.low > 0
            assert b.high >= b.low
            assert b.low <= b.open <= b.high
            assert b.low <= b.close <= b.high
            assert b.volume >= 0

    def test_empty_response(self):
        """空响应返回空列表,不抛异常。

        子类需要让 make_provider 在被查询 999999.SZ 时返回 empty_response.json。
        若子类做不到这种区分,可以重写此测试。
        """
        bars = self.provider.get_daily_bars(["999999.SZ"], date(2026, 7, 14), date(2026, 7, 20))
        assert bars == []

    def test_provider_has_name(self):
        assert self.provider.name in ("eastmoney", "akshare", "baostock")
