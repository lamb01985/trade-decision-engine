from datetime import datetime, timedelta, timezone

from app.engines.setups import build_standard_decision, detect_candidates
from app.models import (ActionNow, Bar, InstrumentAnalysis, LevelImportance, PriceLevel,
    SetupFamily, SetupProgress, TimeframeAnalysis, Trend)


def frame(trend=Trend.BULLISH, price=100.8, vwap=100, rvol=1.5):
    return TimeframeAnalysis(timeframe="5m", price=price, vwap=vwap, ema_9=100.2, ema_21=100,
        vwap_state="ABOVE" if price > vwap else "BELOW", ema_alignment=trend.value,
        distance_from_vwap=price-vwap, distance_from_vwap_pct=(price-vwap)/vwap*100,
        trend=trend, volume=5000, relative_volume=rvol)


def instrument(price=100.8, trend=Trend.BULLISH, rvol=1.5):
    f = frame(trend, price, rvol=rvol)
    return InstrumentAnalysis(symbol="TEST", available=True, as_of=datetime(2026, 8, 14, 14, 9, tzinfo=timezone.utc),
        timeframes={x: f.model_copy(update={"timeframe": x, "price": price}) for x in ("1m", "5m", "15m")}, atr_5m=.5)


def mapped(price, kind):
    return PriceLevel(price=price, types=[kind], importance=LevelImportance.RESISTANCE,
        distance=price-100.8, distance_pct=(price-100.8)/100.8*100,
        position="ABOVE" if price > 100.8 else "BELOW")


def bars(last_three, base=100.0):
    values = [(base, base+.08, base-.08, base)] * 6 + last_three
    start = datetime(2026, 8, 14, 13, 34, tzinfo=timezone.utc)
    return [Bar(timestamp=start+timedelta(minutes=i*5), open=o, high=h, low=l, close=c, volume=5000)
            for i, (o, h, l, c) in enumerate(values)]


def families(inst, levels, sequence):
    return {x.family: x for x in detect_candidates(inst, levels, bars(sequence))}


def test_touching_level_and_indicator_alignment_do_not_confirm():
    seq = [(100.6,100.8,100.5,100.7),(100.7,101,100.6,100.9),(100.9,101,100.7,100.9)]
    found = families(instrument(.9), [mapped(101,"OPENING_RANGE_HIGH"),mapped(99,"SUPPORT"),mapped(103,"TARGET")], seq)
    assert found[SetupFamily.OPENING_RANGE_BREAKOUT].state != SetupProgress.CONFIRMED


def test_breakout_without_acceptance_retest_is_triggered_only():
    seq = [(100.5,100.7,100.4,100.6),(100.7,100.9,100.6,100.8),(100.9,101.3,100.8,101.2)]
    found = families(instrument(101.2), [mapped(101,"OPENING_RANGE_HIGH"),mapped(99,"SUPPORT"),mapped(103,"TARGET")], seq)
    assert found[SetupFamily.OPENING_RANGE_BREAKOUT].state == SetupProgress.TRIGGERED


def test_failed_breakout_and_failed_breakdown_are_detected():
    up = [(101,101.3,100.9,101.2),(101.1,101.2,100.7,100.8),(100.9,101.05,100.6,100.7)]
    assert families(instrument(.7), [mapped(101,"SWING")], up)[SetupFamily.FAILED_BREAKOUT].state == SetupProgress.CONFIRMED
    down = [(99,99.1,98.7,98.8),(98.9,99.3,98.8,99.2),(99.1,99.4,98.95,99.3)]
    assert families(instrument(99.3), [mapped(99,"SWING")], down)[SetupFamily.FAILED_BREAKDOWN].state == SetupProgress.CONFIRMED


def test_vwap_reclaim_and_rejection_logic():
    reclaim = [(99.8,99.9,99.6,99.7),(99.9,100.3,99.8,100.2),(100.1,100.4,100.02,100.3)]
    found = families(instrument(100.3), [mapped(100,"SESSION_VWAP")], reclaim)
    assert found[SetupFamily.VWAP_RECLAIM].state == SetupProgress.CONFIRMED
    reject = [(99.7,99.9,99.6,99.8),(99.9,100.05,99.7,99.8),(99.7,99.75,99.4,99.5)]
    found = families(instrument(99.5, Trend.BEARISH), [mapped(100,"SESSION_VWAP")], reject)
    assert found[SetupFamily.VWAP_REJECTION].state == SetupProgress.CONFIRMED


def test_orb_and_compression_confirmation_require_acceptance():
    orb = [(100.7,101.2,100.6,101.1),(101.1,101.3,101.05,101.2),(101.15,101.4,101.02,101.3)]
    assert families(instrument(101.3), [mapped(101,"OPENING_RANGE_HIGH")], orb)[SetupFamily.OPENING_RANGE_BREAKOUT].state == SetupProgress.CONFIRMED
    compact = [(100,100.08,99.95,100)]*6 + [(100,100.4,100,100.3),(100.25,100.5,100.05,100.4)]
    found = detect_candidates(instrument(100.4), [mapped(99,"SUPPORT"),mapped(101,"TARGET")], bars(compact[-3:]) if False else [
        Bar(timestamp=datetime(2026,8,14,13,34,tzinfo=timezone.utc)+timedelta(minutes=i*5),open=o,high=h,low=l,close=c,volume=5000)
        for i,(o,h,l,c) in enumerate(compact)])
    assert any(x.family == SetupFamily.COMPRESSION_BREAK and x.state == SetupProgress.CONFIRMED for x in found)


def test_opening_range_identity_uses_raw_boundary_without_changing_merged_trigger():
    sequence = [(100.7,101.2,100.6,101.1),(101.1,101.3,101.05,101.2),
                (101.15,101.4,101.02,101.3)]
    raw_boundary = 100.75
    merged_trigger = 101.0
    inst = instrument(101.3).model_copy(update={"opening_range_high": raw_boundary})
    candidate = families(inst, [mapped(merged_trigger, "OPENING_RANGE_HIGH")], sequence)[
        SetupFamily.OPENING_RANGE_BREAKOUT]
    assert candidate.trigger == merged_trigger
    assert candidate.anchor == {"price": raw_boundary, "role": "OPENING_RANGE_HIGH",
                                "opening_range_minutes": 30}


def test_opening_range_low_identity_uses_raw_boundary_without_changing_merged_trigger():
    sequence = [(99.3,99.4,98.8,98.9),(98.9,98.95,98.6,98.8),
                (98.85,98.98,98.5,98.7)]
    raw_boundary = 99.25
    merged_trigger = 99.0
    inst = instrument(98.7, Trend.BEARISH).model_copy(update={"opening_range_low": raw_boundary})
    candidate = families(inst, [mapped(merged_trigger, "OPENING_RANGE_LOW")], sequence)[
        SetupFamily.OPENING_RANGE_BREAKDOWN]
    assert candidate.trigger == merged_trigger
    assert candidate.anchor == {"price": raw_boundary, "role": "OPENING_RANGE_LOW",
                                "opening_range_minutes": 30}


def test_state_can_become_invalidated_or_too_late_and_targets_are_mapped():
    seq = [(100.7,101.2,100.6,101.1),(101.1,101.3,101.05,101.2),(101.15,101.4,101.02,101.3)]
    levels = [mapped(99,"SUPPORT"),mapped(101,"OPENING_RANGE_HIGH"),mapped(102,"SWING"),mapped(103,"TARGET")]
    first = build_standard_decision(instrument(101.3), levels, bars(seq), 90)
    assert first.target_1 in {x.price for x in levels} and first.target_2 in {x.price for x in levels}
    invalid_prior = first.model_copy(update={"invalidation": 102, "setup_state": SetupProgress.CONFIRMED})
    invalid = build_standard_decision(instrument(101.3), levels, bars(seq), 90, invalid_prior)
    assert invalid.setup_state == SetupProgress.INVALIDATED
    late = build_standard_decision(instrument(102), levels, bars([(100.7,101.2,100.6,101.1),(101.1,101.3,101.05,101.2),(101.15,102.2,101.02,102)]), 90)
    assert late.setup_state == SetupProgress.TOO_LATE and late.action_now == ActionNow.DO_NOT_CHASE


def test_ambiguous_or_missing_conditions_return_no_trade():
    neutral = instrument(100, Trend.NEUTRAL)
    decision = build_standard_decision(neutral, [mapped(99,"SUPPORT"),mapped(101,"RESISTANCE")], bars([(100,100.1,99.9,100)]*3), 90)
    assert decision.setup_state == SetupProgress.NO_SETUP and decision.action_now == ActionNow.NO_TRADE
