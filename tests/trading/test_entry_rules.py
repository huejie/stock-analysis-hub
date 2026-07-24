import pytest
from backend.trading.strategies.entry_rules import (
    evaluate_entry, compute_trigger_prices,
)
from backend.trading.strategies.scoring import ScoreBreakdown
from backend.trading.services.indicator_service import IndicatorResult


def _breakdown(total=80, hits=None):
    """构造指定 total 的 ScoreBreakdown(调整维度字段达到目标分)。"""
    if total >= 80:
        # 高分:全部维度拉满
        return ScoreBreakdown(
            trend=30, momentum=15, volume_structure=15,
            volatility=10, relative_strength=10, auxiliary=0,
            rule_hits=hits or ["TREND_UP", "NEAR_20D_HIGH"],
        )
    # 低分:只给 trend,其余 0
    return ScoreBreakdown(
        trend=min(30, total), momentum=0, volume_structure=0,
        volatility=0, relative_strength=0, auxiliary=0,
        rule_hits=hits or [],
    )


def _ind(close=10, high_20d=10.1, atr=0.4, low_10d=9.5):
    return IndicatorResult(ma20=9, ma60=8, atr=atr, high_20d=high_20d,
                           low_10d=low_10d, vol_20d=0.03, close=close)


def test_entry_all_conditions_met():
    """市场非 DEFENSE + 评分>=70 + 近高点 + 止损 3-10% -> CONDITIONAL_BUY。"""
    result = evaluate_entry(
        regime="ATTACK", score=_breakdown(80), ind=_ind(close=10, high_20d=10.1),
        entry_price=10.0, atr=0.4, low_10d=9.5,
        portfolio_has_capacity=True, min_score=70,
    )
    assert result.action == "CONDITIONAL_BUY"
    assert result.trigger_prices is not None


def test_entry_defense_blocks():
    """DEFENSE 市场 -> WATCH(不开仓)。"""
    result = evaluate_entry(
        regime="DEFENSE", score=_breakdown(80), ind=_ind(),
        entry_price=10.0, atr=0.4, low_10d=9.5,
        portfolio_has_capacity=True, min_score=70,
    )
    assert result.action == "WATCH"


def test_entry_low_score_watches():
    """评分 < 70 -> WATCH。"""
    result = evaluate_entry(
        regime="ATTACK", score=_breakdown(60), ind=_ind(),
        entry_price=10.0, atr=0.4, low_10d=9.5,
        portfolio_has_capacity=True, min_score=70,
    )
    assert result.action == "WATCH"


def test_entry_no_capacity_forbidden():
    """组合无额度 -> FORBIDDEN。"""
    result = evaluate_entry(
        regime="ATTACK", score=_breakdown(80), ind=_ind(),
        entry_price=10.0, atr=0.4, low_10d=9.5,
        portfolio_has_capacity=False, min_score=70,
    )
    assert result.action == "FORBIDDEN"


def test_entry_stop_too_far():
    """止损距离 > 10% -> WATCH(放弃)。

    entry=10, atr=2.0, low_10d=8.5 -> raw_stop=max(8.5, 10-4)=8.5, distance=15% > 10%。
    """
    result = evaluate_entry(
        regime="ATTACK", score=_breakdown(80), ind=_ind(close=10, low_10d=8.5),
        entry_price=10.0, atr=2.0, low_10d=8.5,
        portfolio_has_capacity=True, min_score=70,
    )
    assert result.action in ("WATCH", "FORBIDDEN")


def test_trigger_prices():
    """触发价 = max(breakout, 信号日高) + tick; 追高价 = 触发价*1.03。"""
    tp = compute_trigger_prices(breakout_level=10.0, signal_day_high=10.2, tick=0.01)
    assert tp.trigger_price == pytest.approx(10.21)  # max(10.0, 10.2)+0.01
    assert tp.do_not_chase_price == pytest.approx(10.21 * 1.03)
