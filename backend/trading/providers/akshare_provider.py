"""AKShare Provider(备用源)。

延用 backend/akshare_fallback.py 的延迟导入模式,首次调用时加载 akshare;
未安装时所有 get_* 抛 ProviderError(retriable=False,因为安装状态不会自动恢复)。

注意:文件名为 akshare_provider.py(非 akshare.py),避免与顶层
backend/akshare_fallback.py 在导入路径上混淆。
"""
from datetime import date, timedelta
import logging

from ..domain import DailyBar, InstrumentStatus, SectorMembership, TradeDay, bare_code
from .base import MarketDataProvider, ProviderError

logger = logging.getLogger("trading.providers.akshare")

# 延迟加载哨兵:区分"从未尝试加载"(初值)与"已尝试但不可用/被外部置 None"。
# 后者应直接返回 None,避免在测试环境(akshare 已安装)中错误地重新导入。
_LOADING = object()


class AkshareProvider:
    """AKShare 备用源 Provider。

    akshare 的 stock_zh_a_hist 返回中文列名(日期/开盘/收盘/最高/最低/成交量/成交额/涨跌幅);
    stock_zh_index_daily_em 返回英文列名(date/open/high/low/close/volume)。
    本 Provider 只做抓取与规范化,不写库。
    """

    name = "akshare"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self._ak = _LOADING  # 延迟加载;外部可注入 fake 模块或显式置 None 表示不可用

    def _ensure_ak(self):
        """首次调用时加载 akshare;若已显式置 None 或加载失败,返回 None。"""
        if self._ak is not _LOADING:
            return self._ak
        try:
            import akshare as ak
            self._ak = ak
            logger.info("akshare %s 已加载", ak.__version__)
            return ak
        except ImportError:
            logger.error("akshare 未安装,备用源不可用")
            self._ak = None
            return None

    def get_daily_bars(self, codes: list[str], start: date, end: date) -> list[DailyBar]:
        ak = self._ensure_ak()
        if ak is None:
            raise ProviderError(self.name, "akshare 未安装", retriable=False)
        results: list[DailyBar] = []
        for code in codes:
            bare = bare_code(code)
            try:
                df = ak.stock_zh_a_hist(
                    symbol=bare, period="daily",
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust="qfq",
                )
            except Exception as e:
                logger.warning("akshare 日线获取失败 %s: %s", code, e)
                raise ProviderError(self.name, f"日线获取失败 {code}: {e}", retriable=True) from e
            if getattr(df, "empty", False):
                continue
            # akshare 返回中文列名,逐行转 DailyBar
            for _, row in df.iterrows():
                try:
                    trade_date = date.fromisoformat(str(row["日期"])[:10])
                    o = float(row["开盘"])
                    h = float(row["最高"])
                    low = float(row["最低"])
                    c = float(row["收盘"])
                    v = float(row["成交量"])
                except (KeyError, ValueError, TypeError):
                    continue
                # OHLC 校验,失败跳过该行
                if not (h >= low and low > 0 and low <= o <= h and low <= c <= h):
                    continue
                try:
                    amount = float(row.get("成交额") or 0) or None
                except (ValueError, TypeError):
                    amount = None
                try:
                    cpct_raw = row.get("涨跌幅")
                    cpct = float(cpct_raw) if cpct_raw is not None else None
                except (ValueError, TypeError):
                    cpct = None
                results.append(DailyBar(
                    code=code, trade_date=trade_date,
                    open=o, high=h, low=low, close=c,
                    volume=v, amount=amount, adjust_factor=1.0,
                    change_pct=cpct, source=self.name,
                ))
        return results

    def get_index_bars(self, codes: list[str], start: date, end: date) -> list[DailyBar]:
        ak = self._ensure_ak()
        if ak is None:
            raise ProviderError(self.name, "akshare 未安装", retriable=False)
        results: list[DailyBar] = []
        for code in codes:
            bare = bare_code(code)
            # akshare 指数代码前缀:sh/sz
            ak_symbol = f"sh{bare}" if code.endswith(".SH") else f"sz{bare}"
            try:
                df = ak.stock_zh_index_daily_em(symbol=ak_symbol)
            except Exception as e:
                logger.warning("akshare 指数获取失败 %s: %s", code, e)
                raise ProviderError(self.name, f"指数获取失败 {code}: {e}", retriable=True) from e
            if getattr(df, "empty", False):
                continue
            # 筛选日期范围(akshare 指数返回英文列:date/open/high/low/close/volume)
            for _, row in df.iterrows():
                try:
                    d = date.fromisoformat(str(row["date"])[:10])
                    if not (start <= d <= end):
                        continue
                    o = float(row["open"]); h = float(row["high"])
                    low = float(row["low"]); c = float(row["close"])
                    v = float(row.get("volume") or 0)
                except (KeyError, ValueError, TypeError):
                    continue
                if not (h >= low and low > 0 and low <= o <= h and low <= c <= h):
                    continue
                results.append(DailyBar(
                    code=code, trade_date=d,
                    open=o, high=h, low=low, close=c,
                    volume=v, source=self.name,
                ))
        return results

    def get_trade_calendar(self, start: date, end: date) -> list[TradeDay]:
        ak = self._ensure_ak()
        if ak is None:
            # 降级:基于周末
            return self._weekend_calendar(start, end)
        try:
            df = ak.tool_trade_date_hist_sina()
            open_dates = {str(r["trade_date"])[:10] for _, r in df.iterrows()}
        except Exception as e:
            logger.warning("akshare 交易日历获取失败,降级周末判断: %s", e)
            return self._weekend_calendar(start, end)
        days = []
        cur = start
        while cur <= end:
            is_open = cur.isoformat() in open_dates
            days.append(TradeDay(date=cur, is_open=is_open, exchange="SSE"))
            cur += timedelta(days=1)
        return days

    @staticmethod
    def _weekend_calendar(start: date, end: date) -> list[TradeDay]:
        days = []
        cur = start
        while cur <= end:
            days.append(TradeDay(date=cur, is_open=cur.weekday() < 5, exchange="SSE"))
            cur += timedelta(days=1)
        return days

    def get_instrument_status(self, codes: list[str], trade_date: date) -> list[InstrumentStatus]:
        # Phase 1 暂返回默认状态(未停牌);Phase 2 接入实时停牌接口
        return [InstrumentStatus(code=c, trade_date=trade_date) for c in codes]

    def get_sector_membership(self, codes: list[str]) -> list[SectorMembership]:
        # Phase 2 接入(可复用 akshare_fallback.fetch_concept_tags_fallback)
        return []
