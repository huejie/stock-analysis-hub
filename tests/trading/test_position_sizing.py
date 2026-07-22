import pytest
from backend.trading.position_sizing import (
    compute_buy_quantity,
    compute_stop_price,
    effective_risk_per_trade,
    BuyQuantityResult,
)


# ---- compute_stop_price (spec §8.7) ----

def test_stop_distance_within_3_to_10_pct():
    """止损距离在 3%-10% 内,原样使用。"""
    # entry=10, raw_stop=9.5 -> distance=5%, 在范围内
    result = compute_stop_price(entry_price=10.0, raw_stop=9.5)
    assert result.stop_price == 9.5
    assert result.stop_distance_pct == pytest.approx(0.05)
    assert result.is_valid is True


def test_stop_distance_too_close_expands_to_3pct():
    """距离 < 3% 时,扩展为入场价下方 3%。"""
    # entry=10, raw_stop=9.9 -> distance=1%, 扩展到 9.7
    result = compute_stop_price(entry_price=10.0, raw_stop=9.9)
    assert result.stop_price == pytest.approx(9.7)
    assert result.stop_distance_pct == pytest.approx(0.03)
    assert result.is_valid is True


def test_stop_distance_too_far_invalid():
    """距离 > 10% 时,不生成买入。"""
    # entry=10, raw_stop=8.5 -> distance=15%, 无效
    result = compute_stop_price(entry_price=10.0, raw_stop=8.5)
    assert result.is_valid is False
    assert result.stop_price is None


# ---- compute_buy_quantity (spec §8.7) ----

def test_buy_quantity_normal_case():
    """正常情况:risk_quantity < cap_quantity。"""
    # total_equity=100000, risk_per_trade=0.005 -> risk_budget=500
    # entry=10, stop=9.5 -> per_share_risk=0.5
    # risk_quantity = floor(500/0.5/100)*100 = floor(10)*100 = 1000
    # cap = floor(100000*0.15/10/100)*100 = floor(15)*100 = 1500
    # buy = min(1000, 1500) = 1000
    result = compute_buy_quantity(
        total_equity=100000, cash=100000,
        entry_price=10.0, stop_price=9.5,
        effective_risk_per_trade=0.005,
        max_single_position=0.15,
    )
    assert result.buy_quantity == 1000
    assert result.risk_quantity == 1000
    assert result.cap_quantity == 1500
    assert result.risk_amount == pytest.approx(500.0)


def test_buy_quantity_capped_by_position_limit():
    """cap_quantity < risk_quantity 时,受仓位上限限制。"""
    # total_equity=100000, risk_per_trade=0.02(激进) -> risk_budget=2000
    # entry=10, stop=9.5 -> risk_quantity = floor(2000/0.5/100)*100 = 4000
    # cap = floor(100000*0.15/10/100)*100 = 1500
    # buy = min(4000, 1500) = 1500
    result = compute_buy_quantity(
        total_equity=100000, cash=100000,
        entry_price=10.0, stop_price=9.5,
        effective_risk_per_trade=0.02,
        max_single_position=0.15,
    )
    assert result.buy_quantity == 1500
    assert result.capped_by == "position_limit"


def test_buy_quantity_capped_by_cash():
    """现金不足时,受现金限制。"""
    # total_equity=100000, cash=5000(只够买 500 股)
    # risk_quantity=1000, cap_quantity=1500, 但现金只够 5000/10=500
    result = compute_buy_quantity(
        total_equity=100000, cash=5000,
        entry_price=10.0, stop_price=9.5,
        effective_risk_per_trade=0.005,
        max_single_position=0.15,
    )
    assert result.buy_quantity == 500
    assert result.capped_by == "cash"


def test_buy_quantity_below_100_no_trade():
    """buy_quantity < 100 时,不生成买入。"""
    # total_equity=1000(小账户), risk_per_trade=0.005 -> risk_budget=5
    # entry=10, stop=9.5 -> risk_quantity = floor(5/0.5/100)*100 = floor(0.1)*100 = 0
    result = compute_buy_quantity(
        total_equity=1000, cash=1000,
        entry_price=10.0, stop_price=9.5,
        effective_risk_per_trade=0.005,
        max_single_position=0.15,
    )
    assert result.buy_quantity == 0
    assert result.is_tradable is False


def test_buy_quantity_rounds_to_100_lots():
    """A 股必须 100 股整数倍。"""
    # risk_budget=550, per_share_risk=0.5 -> 550/0.5=1100 股 -> floor(1100/100)*100=1100
    result = compute_buy_quantity(
        total_equity=110000, cash=110000,
        entry_price=10.0, stop_price=9.5,
        effective_risk_per_trade=0.005,
        max_single_position=0.15,
    )
    assert result.buy_quantity == 1100
    assert result.buy_quantity % 100 == 0


# ---- effective_risk_per_trade (spec §8.2/8.4/8.7) ----

def test_effective_risk_no_reduction():
    """无连续亏损、进攻市场:使用 base risk。"""
    assert effective_risk_per_trade(
        base_risk=0.005, consecutive_losses=0, regime="ATTACK", degraded=False
    ) == pytest.approx(0.005)


def test_effective_risk_consecutive_loss_reduction():
    """连续亏损 >=3 笔:降至 0.25%。"""
    assert effective_risk_per_trade(
        base_risk=0.005, consecutive_losses=3, regime="ATTACK", degraded=False
    ) == pytest.approx(0.0025)


def test_effective_risk_neutral_halves():
    """中性市场:减半。"""
    assert effective_risk_per_trade(
        base_risk=0.005, consecutive_losses=0, regime="NEUTRAL", degraded=False
    ) == pytest.approx(0.0025)


def test_effective_risk_degraded_halves():
    """数据降级:减半。"""
    assert effective_risk_per_trade(
        base_risk=0.005, consecutive_losses=0, regime="ATTACK", degraded=True
    ) == pytest.approx(0.0025)


def test_effective_risk_floor_0_1pct():
    """有效单笔风险不低于 0.1%。"""
    # base=0.001, neutral+degraded+3losses -> 0.001*0.5*0.5=0.00025, 但 floor=0.001
    result = effective_risk_per_trade(
        base_risk=0.001, consecutive_losses=3, regime="NEUTRAL", degraded=True
    )
    assert result == pytest.approx(0.001)  # floor


def test_effective_risk_ceiling():
    """有效单笔风险不高于配置上限。"""
    result = effective_risk_per_trade(
        base_risk=0.005, consecutive_losses=0, regime="ATTACK", degraded=False,
        max_risk=0.004
    )
    assert result == pytest.approx(0.004)
