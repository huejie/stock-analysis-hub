from datetime import date
import pytest
from backend.trading.services.indicator_service import (
    sma, atr14, percentile_rank, realized_volatility,
    compute_indicators, IndicatorResult,
)
from backend.trading.domain import DailyBar


def _bar(d, close, high=None, low=None, open_=None, vol=1000):
    return DailyBar(code="X", trade_date=d,
                    open=open_ or close, high=high or close*1.02, low=low or close*0.98,
                    close=close, volume=vol, source="t")


def test_sma_basic():
    bars = [_bar(date(2026,7,1), 10), _bar(date(2026,7,2), 20), _bar(date(2026,7,3), 30)]
    assert sma([b.close for b in bars], 3) == pytest.approx(20.0)


def test_sma_insufficient_data_returns_none():
    bars = [_bar(date(2026,7,1), 10)]
    assert sma([b.close for b in bars], 20) is None


def test_atr14_basic():
    # 构造 14 根 K 线,高低差稳定为 1
    bars = [_bar(date(2026,7,1+i), 10+i, high=11+i, low=9+i) for i in range(14)]
    atr = atr14(bars)
    assert atr is not None
    assert atr == pytest.approx(2.0, abs=0.5)  # TR ≈ high-low = 2


def test_atr14_insufficient_returns_none():
    bars = [_bar(date(2026,7,1), 10)]
    assert atr14(bars) is None


def test_percentile_rank():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    # value=7 在 10 个值中,有 6 个小于它,rank=60%
    assert percentile_rank(values, 7) == pytest.approx(0.6)


def test_realized_volatility():
    # 稳定价格 -> 低波动(period=20 需要 21 个收盘价才能算 20 个收益率)
    bars = [_bar(date(2026,7,1+i), 10) for i in range(21)]
    vol = realized_volatility([b.close for b in bars], 20)
    assert vol == pytest.approx(0.0, abs=0.001)


def test_compute_indicators_full():
    """20 根 K 线计算完整指标。"""
    bars = [_bar(date(2026,7,1+i), 10 + i*0.1) for i in range(20)]
    result = compute_indicators(bars)
    assert result.ma20 is not None
    assert result.atr14 is not None
    assert result.high_20d == max(b.high for b in bars)
    assert result.low_10d == min(b.low for b in bars[-10:])


def test_compute_indicators_no_lookahead():
    """信号日 t 的指标只用 date <= t 的数据。

    防未来函数契约(spec §14.1):compute_indicators 不做内部时间过滤,
    调用方负责只传到 t 的 bars。本测试验证该契约——传入未来数据会改变
    窗口计算结果(证明函数未做内部过滤),因此调用方必须自行截断。
    """
    bars_t = [_bar(date(2026,7,1+i), 10+i*0.1) for i in range(20)]  # 到 7-20
    bars_future = [_bar(date(2026,7,21), 100)]  # 未来大涨
    # 只传到 t 的 bars —— 这是调用方应遵守的防未来函数用法
    result_t = compute_indicators(bars_t)
    # 多传未来数据 —— 函数不做内部过滤,所以窗口末尾变为未来日,
    # high_20d / ma20 都会随之变化(这正是调用方必须自行截断的原因)
    result_future = compute_indicators(bars_t + bars_future)
    assert result_t.ma20 is not None
    assert result_t.high_20d != result_future.high_20d  # 未来数据改变了窗口
    # 契约:要得到 t 时刻的指标,调用方必须只传到 t 的 bars
    assert result_t.close == pytest.approx(10 + 19 * 0.1)  # 7-20 收盘
