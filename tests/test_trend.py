from app.engines.market import classify_trend
from app.models import Trend


def test_bullish_trend_requires_all_conditions():
    assert classify_trend(105, 100, 103, 101) == Trend.BULLISH


def test_bearish_trend_requires_all_conditions():
    assert classify_trend(95, 100, 97, 99) == Trend.BEARISH


def test_mixed_conditions_are_neutral():
    assert classify_trend(105, 100, 99, 101) == Trend.NEUTRAL

