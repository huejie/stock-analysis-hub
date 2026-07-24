import pytest
from backend.trading.strategies.exit_rules import (
    evaluate_exit, compute_trailing_stop,
)


def test_trailing_stop_only_moves_up():
    """移动止损只上移不下移(spec §15.1)。"""
    # 持仓最高收盘 12, ATR=0.4 -> trailing = 12 - 2.5*0.4 = 11
    new_stop = compute_trailing_stop(prev_trailing=10.5, highest_close=12.0, atr=0.4)
    assert new_stop == pytest.approx(11.0)
    # 上一日已经是 11.1,今日算出 11.0 -> 取 max,不降
    new_stop2 = compute_trailing_stop(prev_trailing=11.1, highest_close=12.0, atr=0.4)
    assert new_stop2 == pytest.approx(11.1)  # 不降


def test_exit_initial_stop_hit():
    """收盘 < 初始止损 -> EXIT。"""
    result = evaluate_exit(
        close=9.0, initial_stop=9.5, trailing_stop=None,
        ma20=10, ma60=9, prev_close=10, prev_prev_close=10,
        entry_price=10, atr=0.4, highest_close=10,
    )
    assert result.action == "EXIT"
    assert "INITIAL_STOP" in result.reason


def test_exit_trend_failure_consecutive_below_ma20():
    """连续 2 日收盘 < MA20 -> EXIT(趋势失效)。"""
    result = evaluate_exit(
        close=9.0, initial_stop=8.0, trailing_stop=None,
        ma20=10, ma60=9, prev_close=9.1, prev_prev_close=9.2,  # 连续低于 MA20
        entry_price=10, atr=0.4, highest_close=10,
    )
    # close=9.0 < ma60=9? 不,9.0 < 9 是 False。改用更低 ma60 测试 BELOW_MA60,
    # 这里 close=9.0 < ma20=10 且 prev_close=9.1 < ma20=10 -> TREND_FAILURE
    assert result.action == "EXIT"
    assert "TREND_FAILURE" in result.reason


def test_exit_single_day_below_ma60():
    """单日收盘 < MA60 -> EXIT。"""
    result = evaluate_exit(
        close=8.0, initial_stop=5.0, trailing_stop=None,
        ma20=10, ma60=9, prev_close=10, prev_prev_close=10,
        entry_price=10, atr=0.4, highest_close=10,
    )
    assert result.action == "EXIT"
    assert "BELOW_MA60" in result.reason


def test_hold_when_no_exit_triggered():
    """无退出条件 -> HOLD。"""
    result = evaluate_exit(
        close=11.0, initial_stop=9.0, trailing_stop=10.0,
        ma20=10, ma60=9, prev_close=11, prev_prev_close=11,
        entry_price=10, atr=0.4, highest_close=11.5,
    )
    assert result.action == "HOLD"


def test_reduce_at_2r():
    """达到 +2R -> REDUCE(建议卖 1/3)。"""
    result = evaluate_exit(
        close=12.0, initial_stop=9.0, trailing_stop=10.0,
        ma20=10, ma60=9, prev_close=11, prev_prev_close=11,
        entry_price=10, atr=0.4, highest_close=12.0,
        target_2r_price=12.0,  # 正好 2R
    )
    assert result.action == "REDUCE"
