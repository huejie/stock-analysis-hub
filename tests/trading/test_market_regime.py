from datetime import date, timedelta
import pytest
from backend.trading.strategies.market_regime import (
    compute_market_score, classify_regime, MarketRegimeResult,
)
from backend.trading.domain import DailyBar


def _day(i):
    """生成从 2026-07-01 起的第 i 天(0-based),避免月份溢出。"""
    return date(2026, 7, 1) + timedelta(days=i)


def _bar(d, close, high=None, low=None):
    return DailyBar(code="000300.SH", trade_date=d, open=close,
                    high=high or close*1.01, low=low or close*0.99,
                    close=close, volume=1e8, source="t")


def test_score_all_positive_attack():
    """M1+M2+M3 正,M4/M5 不触发 -> score >= 2 -> ATTACK。"""
    # 收盘 > MA20, MA20 > MA60, 宽度 55%+, 波动低, 收盘 > MA60
    bars = [_bar(_day(i), 100 + i) for i in range(60)]  # 上升趋势
    result = compute_market_score(
        benchmark_bars=bars,
        breadth_ratio=0.60,  # 60% > 55%
        volatility_percentile=0.50,  # 波动中等,不在 80% 分位以上
    )
    assert result.score >= 2
    assert result.regime == "ATTACK"


def test_score_neutral_boundary():
    """score=0 或 1 -> NEUTRAL。"""
    bars = [_bar(_day(i), 100) for i in range(60)]  # 平盘
    result = compute_market_score(
        benchmark_bars=bars, breadth_ratio=0.50, volatility_percentile=0.50,
    )
    # 平盘:收盘=MA20(M1=0), MA20=MA60(M2=0), 宽度<55%(M3=0), 波动低(M4=0), 收盘=MA60(M5=0)
    # score=0 -> NEUTRAL
    assert result.score == 0
    assert result.regime == "NEUTRAL"


def test_score_defense():
    """收盘 < MA60, 下跌趋势 -> score <= -1 -> DEFENSE。"""
    bars = [_bar(_day(i), 100 - i) for i in range(60)]  # 下降趋势
    result = compute_market_score(
        benchmark_bars=bars, breadth_ratio=0.40, volatility_percentile=0.90,  # 高波动
    )
    assert result.score <= -1
    assert result.regime == "DEFENSE"


def test_degraded_when_breadth_none():
    """宽度数据不可用 -> degraded=True。"""
    bars = [_bar(_day(i), 100+i) for i in range(60)]
    result = compute_market_score(
        benchmark_bars=bars, breadth_ratio=None, volatility_percentile=0.50,
    )
    assert result.degraded is True


def test_recommended_exposure_by_regime():
    """regime → 建议总仓位。"""
    assert classify_regime(2).recommended_exposure == 0.60
    assert classify_regime(1).recommended_exposure == 0.40
    assert classify_regime(0).recommended_exposure == 0.40
    assert classify_regime(-1).recommended_exposure == 0.20
