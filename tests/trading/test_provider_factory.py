from types import SimpleNamespace

import pytest

from backend.trading.errors import ProviderConfigInvalidError
from backend.trading.providers.akshare_provider import AkshareProvider
from backend.trading.providers.composite import CompositeProvider
from backend.trading.providers.eastmoney import EastmoneyProvider
from backend.trading.providers.factory import get_provider


def _settings(priority="eastmoney,akshare"):
    return SimpleNamespace(
        trading_provider_priority=priority,
        trading_provider_timeout_seconds=7.5,
        trading_provider_max_retries=2,
    )


def test_factory_preserves_priority_and_parameters():
    provider = get_provider(_settings())

    assert isinstance(provider, CompositeProvider)
    assert [type(item) for item in provider._providers] == [
        EastmoneyProvider,
        AkshareProvider,
    ]
    assert [item.timeout for item in provider._providers] == [7.5, 7.5]
    assert provider.max_retries == 2


def test_factory_rejects_unknown_provider():
    with pytest.raises(ProviderConfigInvalidError) as exc:
        get_provider(_settings("eastmoney,unknown"))

    assert exc.value.code == "PROVIDER_CONFIG_INVALID"
    assert exc.value.details["provider"] == "unknown"


@pytest.mark.parametrize("priority", ["", " , "])
def test_factory_rejects_empty_priority(priority):
    with pytest.raises(ProviderConfigInvalidError):
        get_provider(_settings(priority))
