"""Provider 工厂:按 config 构造 CompositeProvider 单例。

熔断状态(哪个源失败、何时解禁)需跨请求保留,故用模块级单例。
data-jobs、scheduler、plan 生成共用同一 Provider 实例(Web 进程内)。
"""
import logging

from ...config import settings
from .composite import CompositeProvider
from .eastmoney import EastmoneyProvider
from .akshare_provider import AkshareProvider

logger = logging.getLogger("trading.providers.factory")

_provider_instance: CompositeProvider | None = None


def get_provider() -> CompositeProvider:
    """返回 CompositeProvider 单例(优先级/超时/重试读 config)。

    默认按 trading_provider_priority("eastmoney,akshare")构造,
    超时 trading_provider_timeout_seconds,重试 trading_provider_max_retries。
    """
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance

    priority = [p.strip() for p in settings.trading_provider_priority.split(",") if p.strip()]
    timeout = settings.trading_provider_timeout_seconds
    max_retries = settings.trading_provider_max_retries

    provider_builders = {
        "eastmoney": lambda: EastmoneyProvider(timeout=timeout),
        "akshare": lambda: AkshareProvider(timeout=timeout),
    }
    providers = [provider_builders[name]() for name in priority if name in provider_builders]
    if not providers:
        # 兜底:config 配错时至少有东方财富
        logger.warning("trading_provider_priority 未匹配任何已知 Provider,使用默认 eastmoney")
        providers = [EastmoneyProvider(timeout=timeout)]

    _provider_instance = CompositeProvider(providers, max_retries=max_retries)
    logger.info("CompositeProvider 已创建: 优先级=%s, timeout=%s, retries=%d",
                priority, timeout, max_retries)
    return _provider_instance


def reset_provider() -> None:
    """重置单例(测试用)。"""
    global _provider_instance
    _provider_instance = None
