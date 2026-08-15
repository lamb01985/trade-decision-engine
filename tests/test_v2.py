from datetime import datetime, timedelta, timezone

from app.engines.decision import build_symbol_trade_plan, evaluate_symbol_setup, score_setup
from app.engines.levels import RawLevel, build_levels, deduplicate_levels
from app.engines.market import analyze_symbol
from app.models import Bar, LevelImportance, SetupState


def market_bars(symbol_price=100.0):
    bars = []
    start = datetime(2026, 8, 12, 13, 30, tzinfo=timezone.utc)
    for day in range(2):
        for minute in range(90):
            price = symbol_price + day * 2 + minute * .03
            timestamp = start + timedelta(days=day, minutes=minute)
            bars.append(Bar(timestamp=timestamp, open=price-.02, high=price+.08, low=price-.08, close=price, volume=1000+minute*10))
    return bars


def test_generic_ticker_analysis_uses_requested_symbol():
    result = analyze_symbol("NVDA", market_bars())
    assert result.available and result.symbol == "NVDA"
    assert {"1m", "5m", "15m"} == set(result.timeframes)


def test_level_deduplication_combines_close_independent_reasons():
    result = deduplicate_levels([RawLevel(100, "VWAP"), RawLevel(100.03, "EMA_9"), RawLevel(101, "HOD")], 100)
    assert len(result) == 2
    assert result[0][1] == ["VWAP", "EMA_9"]


def test_levels_are_classified_relative_to_price_and_ordered():
    bars = market_bars()
    instrument = analyze_symbol("AMD", bars)
    levels = build_levels(instrument, bars[-90:])
    assert levels == sorted(levels, key=lambda x: x.price, reverse=True)
    assert all(level.importance in LevelImportance for level in levels)
    assert all(x.importance in {LevelImportance.RESISTANCE, LevelImportance.MAJOR_RESISTANCE} for x in levels if x.position == "ABOVE")


def test_setup_score_is_explainable_and_cannot_confirm_setup():
    bars = market_bars()
    instrument = analyze_symbol("NVDA", bars)
    levels = build_levels(instrument, bars[-90:])
    setup = evaluate_symbol_setup(instrument, levels)
    plan = build_symbol_trade_plan(instrument, setup, levels)
    score = score_setup(instrument, setup, levels, plan)
    assert 0 <= score.score <= 100 and score.factors
    if setup.state in {SetupState.LONG_CONFIRMED, SetupState.SHORT_CONFIRMED}:
        assert setup.trigger_occurred
