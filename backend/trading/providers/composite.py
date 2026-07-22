"""Composite Provider:主备切换 + 重试 + 熔断。

设计(文档 7.5):
- Provider 采用指数退避,最多重试 max_retries 次;不同数据源的重试互不嵌套。
- 单源失败后进入短暂熔断,避免连续请求触发封禁。
- 主源返回空列表视为"成功但无数据",不触发 failover(只有抛错才切换)。
"""
import logging
import time
from datetime import date

from ..domain import DailyBar, InstrumentStatus, SectorMembership, TradeDay
from .base import ProviderError, ProviderUnavailable

logger = logging.getLogger("trading.providers.composite")


class CompositeProvider:
    """按优先级组合多个 Provider。

    用法:CompositeProvider([eastmoney, akshare], max_retries=3)
    """

    name = "composite"

    def __init__(self, providers: list, max_retries: int = 3,
                 retry_base_delay: float = 0.5):
        if not providers:
            raise ValueError("providers 不能为空")
        self._providers = providers
        self.max_retries = max(1, max_retries)
        self.retry_base_delay = retry_base_delay
        # 熔断状态:provider_name -> 解禁时间戳
        self._circuit_until: dict[str, float] = {}

    def _call_with_failover(self, method_name: str, *args, **kwargs):
        """按优先级尝试各 provider,主源抛 retriable 错误时重试,最终全部失败抛 ProviderUnavailable。"""
        last_error: Exception | None = None
        for provider in self._providers:
            # 熔断检查
            until = self._circuit_until.get(provider.name, 0)
            if time.time() < until:
                logger.info("provider %s 处于熔断期,跳过", provider.name)
                continue

            for attempt in range(1, self.max_retries + 1):
                try:
                    method = getattr(provider, method_name)
                    return method(*args, **kwargs)
                except ProviderError as e:
                    last_error = e
                    if not e.retriable:
                        logger.warning("provider %s 不可重试错误,切换: %s", provider.name, e)
                        # 不可重试(如未安装),熔断较长时间并切下一个
                        self._circuit_until[provider.name] = time.time() + 300
                        break
                    if attempt < self.max_retries:
                        delay = self.retry_base_delay * (2 ** (attempt - 1))
                        logger.info("provider %s 第 %d 次重试(%.2fs 后): %s",
                                    provider.name, attempt, delay, e)
                        time.sleep(delay)
                    else:
                        logger.warning("provider %s 重试 %d 次仍失败,切换", provider.name, attempt)
                        self._circuit_until[provider.name] = time.time() + 60
                except Exception as e:
                    last_error = e
                    logger.warning("provider %s 未知异常,切换: %s", provider.name, e)
                    self._circuit_until[provider.name] = time.time() + 60
                    break

        raise ProviderUnavailable(
            f"所有数据源不可用,最后错误: {last_error}"
        )

    def get_daily_bars(self, codes: list[str], start: date, end: date) -> list[DailyBar]:
        return self._call_with_failover("get_daily_bars", codes, start, end)

    def get_index_bars(self, codes: list[str], start: date, end: date) -> list[DailyBar]:
        return self._call_with_failover("get_index_bars", codes, start, end)

    def get_trade_calendar(self, start: date, end: date) -> list[TradeDay]:
        return self._call_with_failover("get_trade_calendar", start, end)

    def get_instrument_status(self, codes: list[str], trade_date: date) -> list[InstrumentStatus]:
        return self._call_with_failover("get_instrument_status", codes, trade_date)

    def get_sector_membership(self, codes: list[str]) -> list[SectorMembership]:
        return self._call_with_failover("get_sector_membership", codes)
