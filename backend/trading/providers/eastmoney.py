"""东方财富行情 Provider(主源)。

复用项目现有东方财富调用模式(参考 backend/lhb_crawler.py):
- 使用 httpx 同步客户端
- 腾讯/东方财富 K 线接口(免登录)
- 日线返回前复权序列用于指标计算

K 线接口采用 web.ifzq.gtimg.cn(appstock)的公开接口,响应结构稳定,与项目
crawler.py 中 TENCENT_KLINE 一致;字段顺序(date,open,close,high,low,volume,amount,af,cpct)。
"""
from datetime import date
import logging

import httpx

from ..domain import (
    DailyBar,
    InstrumentStatus,
    SectorMembership,
    TradeDay,
    bare_code,
    normalize_stock_code,
)
from .base import MarketDataProvider, ProviderError

logger = logging.getLogger("trading.providers.eastmoney")

# 腾讯前复权 K 线(项目 crawler.py 已验证可用)
_KLINE_API = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


class EastmoneyProvider:
    """东方财富/腾讯公开接口 Provider。

    命名沿用文档约定(项目内 eastmoney 为数据源统称)。实际 K 线走腾讯接口,
    龙虎榜与概念板块走东方财富 datacenter(本 Provider Phase 1 只实现 K 线,
    板块/状态等延后到 Phase 2 需要)。
    """

    name = "eastmoney"

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    # ---- 内部 HTTP 抽象(便于测试 monkeypatch) ----

    def _http_get(self, url: str, **kwargs) -> dict:
        """同步 GET,返回 JSON。失败抛 httpx 异常(由调用方转 ProviderError)。"""
        resp = httpx.get(url, headers=_HEADERS, timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _tencent_symbol(self, code: str) -> str:
        """000001.SZ -> ``sz000001`` (腾讯 fqkline 标识)。"""
        exchange = code.rsplit(".", 1)[-1].lower()
        return f"{exchange}{bare_code(code)}"

    def _extract_klines(self, payload: dict, symbol: str, code: str) -> list:
        """校验腾讯响应协议并返回请求证券的 K 线数组。"""
        if not isinstance(payload, dict):
            raise ProviderError(
                self.name, f"腾讯 K 线协议错误 {code}: 顶层响应不是对象"
            )

        message = payload.get("msg")
        if payload.get("code") != 0 or not isinstance(message, str) or message:
            detail = (
                message
                if isinstance(message, str) and message
                else repr(payload.get("code"))
            )
            raise ProviderError(
                self.name, f"腾讯 K 线协议错误 {code}: {detail}"
            )

        data_node = payload.get("data")
        if not isinstance(data_node, dict):
            raise ProviderError(
                self.name, f"腾讯 K 线协议错误 {code}: data 不是对象"
            )

        code_node = data_node.get(symbol)
        if code_node is None:
            return []
        if not isinstance(code_node, dict):
            raise ProviderError(
                self.name, f"腾讯 K 线协议错误 {code}: 证券节点不是对象"
            )

        qfqday = code_node.get("qfqday")
        if qfqday is None or (isinstance(qfqday, list) and not qfqday):
            klines = code_node.get("day", qfqday or [])
        else:
            klines = qfqday
        if not isinstance(klines, list):
            raise ProviderError(
                self.name, f"腾讯 K 线协议错误 {code}: K 线节点不是数组"
            )
        return klines

    # ---- K 线解析 ----

    def _parse_kline_line(self, code: str, line: str) -> DailyBar | None:
        """解析一行 kline 字符串。

        格式:date,open,close,high,low,volume,amount,adjust_factor,change_pct
        (腾讯 fqkline 返回的字段顺序)
        """
        parts = line.split(",")
        if len(parts) < 6:
            return None
        try:
            trade_date = date.fromisoformat(parts[0])
            o, c, h, l = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
            v = float(parts[5])
            amount = float(parts[6]) if len(parts) > 6 and parts[6] else None
            af = float(parts[7]) if len(parts) > 7 and parts[7] else 1.0
            cpct = float(parts[8]) if len(parts) > 8 and parts[8] else None
        except (ValueError, IndexError):
            return None
        # OHLC 基础校验,失败跳过该行
        if not (h >= l and l > 0 and l <= o <= h and l <= c <= h):
            return None
        return DailyBar(
            code=code, trade_date=trade_date,
            open=o, high=h, low=l, close=c,
            volume=v, amount=amount, adjust_factor=af, change_pct=cpct,
            source=self.name,
        )

    # ---- MarketDataProvider 实现 ----

    def get_daily_bars(self, codes: list[str], start: date, end: date) -> list[DailyBar]:
        results: list[DailyBar] = []
        for raw_code in codes:
            code = normalize_stock_code(raw_code, kind="stock")
            symbol = self._tencent_symbol(code)
            try:
                payload = self._http_get(_KLINE_API, params={
                    "param": f"{symbol},day,{start.isoformat()},{end.isoformat()},640,qfq",
                })
            except Exception as e:
                logger.warning("eastmoney 日线获取失败 %s: %s", code, e)
                raise ProviderError(self.name, f"日线获取失败 {code}: {e}", retriable=True) from e

            # 腾讯结构:data -> {symbol: {qfqday: [[...], ...]}} 或 {day: [[...]]}
            klines = self._extract_klines(payload, symbol, code)

            for line in klines:
                # klines 元素可能是字符串(逗号分隔)或 list(腾讯返回 list of lists)
                if isinstance(line, list):
                    line = ",".join(str(x) for x in line)
                elif not isinstance(line, str):
                    raise ProviderError(
                        self.name,
                        f"腾讯 K 线协议错误 {code}: K 线行不是字符串或数组",
                    )
                bar = self._parse_kline_line(code, line)
                if bar and start <= bar.trade_date <= end:
                    results.append(bar)
        return results

    def get_index_bars(self, codes: list[str], start: date, end: date) -> list[DailyBar]:
        """指数 K 线(沪深300/中证500)。接口与股票一致,secid 前缀 1。"""
        normalized_codes = [
            normalize_stock_code(code, kind="index") for code in codes
        ]
        return self.get_daily_bars(normalized_codes, start, end)

    def get_trade_calendar(self, start: date, end: date) -> list[TradeDay]:
        raise ProviderError(
            self.name,
            "该 Provider 不提供可信节假日交易日历",
            retriable=False,
        )

    def get_instrument_status(self, codes: list[str], trade_date: date) -> list[InstrumentStatus]:
        # Phase 1 暂返回默认状态(未停牌);Phase 2 接入实时停牌接口
        return [InstrumentStatus(code=c, trade_date=trade_date) for c in codes]

    def get_sector_membership(self, codes: list[str]) -> list[SectorMembership]:
        # Phase 2 接入(可复用 lhb_crawler.fetch_concept_tags)
        return []
