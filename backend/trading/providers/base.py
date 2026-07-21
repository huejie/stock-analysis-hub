"""市场数据 Provider 统一接口。

所有第三方数据源必须实现此 Protocol,业务服务不直接调用网页接口(文档 7.1)。
Provider 只负责抓取与解析,返回规范化领域对象;不直接写库(由 Repository 负责)。
"""
from datetime import date
from typing import Protocol

from ..domain import DailyBar, InstrumentStatus, SectorMembership, TradeDay


class MarketDataProvider(Protocol):
    """市场数据 Provider 协议。"""

    name: str  # "eastmoney" / "akshare" / "baostock"

    def get_trade_calendar(self, start: date, end: date) -> list[TradeDay]:
        """获取交易日历(start..end,含非交易日标记 is_open=False)。"""
        ...

    def get_daily_bars(self, codes: list[str], start: date, end: date) -> list[DailyBar]:
        """获取股票日线(前复权,adjust_factor 基准序列取 1.0)。

        codes 为规范化形式 000001.SZ。
        """
        ...

    def get_index_bars(self, codes: list[str], start: date, end: date) -> list[DailyBar]:
        """获取指数日线(沪深300/中证500 等)。"""
        ...

    def get_instrument_status(self, codes: list[str], trade_date: date) -> list[InstrumentStatus]:
        """获取证券在某交易日的状态(停牌/ST/涨跌停)。"""
        ...

    def get_sector_membership(self, codes: list[str]) -> list[SectorMembership]:
        """获取股票所属行业/板块。"""
        ...


class ProviderError(Exception):
    """Provider 抓取失败(网络/解析/限流)。"""

    def __init__(self, provider: str, message: str, *, retriable: bool = True):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.retriable = retriable


class ProviderUnavailable(Exception):
    """所有数据源均不可用。"""
