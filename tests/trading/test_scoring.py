from datetime import date
import pytest
from backend.trading.strategies.scoring import compute_score, ScoreBreakdown
from backend.trading.services.indicator_service import IndicatorResult


def _ind(close=10, ma20=9, ma60=8, atr=0.4, high_20d=10.1, vol_20d=0.03):
    return IndicatorResult(ma20=ma20, ma60=ma60, atr=atr, high_20d=high_20d,
                           low_10d=9.5, vol_20d=vol_20d, close=close)


def test_score_trend_dimension():
    """中期趋势 30 分:close>MA20(10), MA20>MA60(10), MA60 斜率正(10)。"""
    ind = _ind(close=10, ma20=9, ma60=8)
    breakdown = compute_score(
        ind, pool_return_percentile=0.5, excess_20d=0.01, excess_60d=0.01,
        volume_ratio=1.5, has_gap=False, auxiliary_bonus=0,
    )
    assert breakdown.trend == 30  # 全部满足


def test_score_momentum_dimension():
    """短期动量 20 分:分位数映射。"""
    ind = _ind()
    breakdown = compute_score(
        ind, pool_return_percentile=0.9, excess_20d=0, excess_60d=0,
        volume_ratio=1.0, has_gap=False, auxiliary_bonus=0,
    )
    assert breakdown.momentum > 0


def test_score_volume_structure():
    """量价结构 20 分:近高点(10)+ 量比(10)。"""
    ind = _ind(close=10, high_20d=10.1)  # close 距高点 < 2%
    breakdown = compute_score(
        ind, pool_return_percentile=0.5, excess_20d=0, excess_60d=0,
        volume_ratio=1.5, has_gap=False, auxiliary_bonus=0,
    )
    assert breakdown.volume_structure == 20  # 两项都满足


def test_score_volatility_dimension():
    """波动风险 15 分:ATR/close 2%-6%(10)+ 无跳空(5)。"""
    ind = _ind(close=10, atr=0.4)  # 4%, 在 2%-6%
    breakdown = compute_score(
        ind, pool_return_percentile=0.5, excess_20d=0, excess_60d=0,
        volume_ratio=1.0, has_gap=False, auxiliary_bonus=0,
    )
    assert breakdown.volatility == 15


def test_score_relative_strength():
    """相对强弱 15 分:20 日超额正(8)+ 60 日超额正(7)。"""
    ind = _ind()
    breakdown = compute_score(
        ind, pool_return_percentile=0.5, excess_20d=0.01, excess_60d=0.01,
        volume_ratio=1.0, has_gap=False, auxiliary_bonus=0,
    )
    assert breakdown.relative_strength == 15


def test_score_total_caps_at_100():
    """总分封顶 100。"""
    ind = _ind(close=10, ma20=9, ma60=8, atr=0.4, high_20d=10.1)
    breakdown = compute_score(
        ind, pool_return_percentile=0.99, excess_20d=0.1, excess_60d=0.1,
        volume_ratio=1.5, has_gap=False, auxiliary_bonus=10,  # 满分辅助
    )
    assert breakdown.total == 100


def test_score_auxiliary_capped_at_10():
    """辅助加分上限 10。"""
    ind = _ind()
    breakdown = compute_score(
        ind, pool_return_percentile=0.5, excess_20d=0, excess_60d=0,
        volume_ratio=1.0, has_gap=False, auxiliary_bonus=50,  # 超大
    )
    assert breakdown.auxiliary == 10
