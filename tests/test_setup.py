from app.engines.decision import build_trade_plan, cross_market_confirmation, evaluate_setup
from app.engines.market import unavailable_nq
from app.models import SetupState, Trend
from tests.helpers import instrument


def test_thesis_without_breakout_never_confirms():
    qqq = instrument(price=109)
    setup = evaluate_setup(qqq, cross_market_confirmation(qqq, unavailable_nq()))
    assert setup.state == SetupState.WATCH_LONG


def test_breakout_without_volume_never_confirms():
    qqq = instrument(price=111, rvol=0.8)
    setup = evaluate_setup(qqq, cross_market_confirmation(qqq, unavailable_nq()))
    assert setup.state == SetupState.WATCH_LONG


def test_aligned_breakout_with_volume_confirms():
    qqq = instrument(price=111)
    setup = evaluate_setup(qqq, cross_market_confirmation(qqq, unavailable_nq()))
    assert setup.state == SetupState.LONG_CONFIRMED


def test_mixed_structure_is_no_trade_and_has_no_plan():
    qqq = instrument()
    qqq.timeframes["15m"].trend = Trend.NEUTRAL
    setup = evaluate_setup(qqq, cross_market_confirmation(qqq, unavailable_nq()))
    assert setup.state == SetupState.NO_TRADE
    assert build_trade_plan(qqq, setup).status == "INSUFFICIENT_CONFIRMATION"

