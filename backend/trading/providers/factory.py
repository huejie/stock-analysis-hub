"""把运行配置转换为统一行情 Provider。"""
from ...config import settings as default_settings
from ..errors import ProviderConfigInvalidError
from .akshare_provider import AkshareProvider
from .composite import CompositeProvider
from .eastmoney import EastmoneyProvider


def get_provider(settings=default_settings) -> CompositeProvider:
    names = [
        name.strip().lower()
        for name in settings.trading_provider_priority.split(",")
        if name.strip()
    ]
    if not names:
        raise ProviderConfigInvalidError("TRADING_PROVIDER_PRIORITY 不能为空")

    factories = {
        "eastmoney": EastmoneyProvider,
        "akshare": AkshareProvider,
    }
    providers = []
    for name in names:
        provider_type = factories.get(name)
        if provider_type is None:
            raise ProviderConfigInvalidError(
                f"不支持的行情 Provider: {name}",
                details={"provider": name, "supported": sorted(factories)},
            )
        providers.append(
            provider_type(timeout=settings.trading_provider_timeout_seconds)
        )
    return CompositeProvider(
        providers,
        max_retries=settings.trading_provider_max_retries,
    )
