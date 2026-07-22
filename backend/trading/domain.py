"""交易模块领域数据类。

所有 Provider 返回的数据必须先规范化为这些类型,业务服务不直接接触第三方字段。
股票代码统一为交易所后缀形式:000001.SZ / 600000.SH / 000300.SH。
"""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Instrument:
    """证券基础信息。"""
    code: str            # 形如 000001.SZ
    name: str
    exchange: str        # SH / SZ
    is_index: bool = False
    sector_name: str | None = None


@dataclass(frozen=True)
class DailyBar:
    """规范化日线 OHLCV。

    - 价格单位:元;成交量单位:股;金额单位:元。
    - adjust_factor 基准:前复权序列取 1.0(指标计算用);原始价格用于成交复盘。
    """
    code: str            # 000001.SZ
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None = None
    pre_close: float | None = None
    change_pct: float | None = None
    adjust_factor: float = 1.0
    source: str = ""


@dataclass(frozen=True)
class TradeDay:
    """交易日历条目。"""
    date: date
    is_open: bool
    exchange: str = "SSE"  # 默认上交所日历(A 股共用)


@dataclass(frozen=True)
class InstrumentStatus:
    """证券在某交易日的状态(停牌/ST/涨跌停等)。"""
    code: str
    trade_date: date
    is_suspended: bool = False
    is_st: bool = False
    is_delisting: bool = False
    limit_up_price: float | None = None
    limit_down_price: float | None = None


@dataclass(frozen=True)
class SectorMembership:
    """股票所属行业/板块。"""
    code: str
    sector_name: str
    sector_type: str = "industry"  # industry / concept


# ---- 代码规范化工具 ----

def normalize_stock_code(raw: str) -> str:
    """将各种输入形式规范化为 000001.SZ / 600000.SH / 000300.SH。

    接受: '000001' / 'sz000001' / '000001.SZ' / 'SZ000001'
    指数: '000300' -> '000300.SH'(沪深300在上交所)
    """
    s = raw.strip().upper().replace(" ", "")
    # 已带后缀
    if s.endswith(".SH") or s.endswith(".SZ"):
        return s
    # 形如 SZ000001
    if s.startswith("SH") or s.startswith("SZ"):
        return f"{s[2:]}.{s[:2]}"
    # 纯 6 位数字
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) == 6:
        # 000xxx 中 0003xx/0009xx 是上交所指数(沪深300/中证500),优先判
        if digits.startswith(("0003", "0009")):
            return f"{digits}.SH"
        # 0xx/3xx(深市主板/创业板)在深交所
        if digits.startswith(("0", "3")):
            return f"{digits}.SZ"
        # 6xx/601/603/605/688 等在上交所
        return f"{digits}.SH"
    raise ValueError(f"无法规范化的股票代码: {raw}")


def bare_code(normalized: str) -> str:
    """000001.SZ -> 000001(调用东方财富等接口时使用)。"""
    return normalized.split(".")[0]


def market_prefix(normalized: str) -> str:
    """东方财富 secid 前缀: 000001.SZ -> '0'; 600000.SH -> '1'。"""
    return "1" if normalized.endswith(".SH") else "0"
