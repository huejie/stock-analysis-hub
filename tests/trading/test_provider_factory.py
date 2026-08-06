"""Provider 工厂测试。

验证 get_provider() 按配置构造 CompositeProvider,且单例行为正确。
"""
from backend.trading.providers.factory import get_provider, reset_provider
from backend.trading.providers.composite import CompositeProvider
from backend.trading.providers.eastmoney import EastmoneyProvider
from backend.trading.providers.akshare_provider import AkshareProvider


def test_get_provider_returns_composite():
    reset_provider()
    p = get_provider()
    assert isinstance(p, CompositeProvider)


def test_get_provider_singleton():
    """重复调用返回同一实例(熔断状态跨调用保留)。"""
    reset_provider()
    p1 = get_provider()
    p2 = get_provider()
    assert p1 is p2


def test_reset_provider_creates_new():
    reset_provider()
    p1 = get_provider()
    reset_provider()
    p2 = get_provider()
    assert p1 is not p2


def test_provider_has_two_providers_by_default():
    """默认 config 是 eastmoney,akshare → 两个 Provider。"""
    reset_provider()
    p = get_provider()
    assert len(p._providers) == 2
    names = [prov.name for prov in p._providers]
    assert "eastmoney" in names
    assert "akshare" in names


def test_provider_priority_order():
    """优先级按 config 顺序。"""
    reset_provider()
    p = get_provider()
    names = [prov.name for prov in p._providers]
    # 默认 config: eastmoney,akshare
    assert names[0] == "eastmoney"
    assert names[1] == "akshare"
