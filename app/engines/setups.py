from dataclasses import dataclass
from datetime import datetime, timezone

from app.engines.market import ET, resample
from app.models import (ActionNow, Bar, DecisionQuality, InstrumentAnalysis, PriceLevel,
    SetupFamily, SetupProgress, StandardDecision, Trend)
from app.parameters import DEFAULT_PARAMETERS, EngineParameters


SETUP_RULES = {
    SetupFamily.OPENING_RANGE_BREAKOUT: "Aligned bullish structure; 5m close above OR high; acceptance/retest and volume confirm.",
    SetupFamily.OPENING_RANGE_BREAKDOWN: "Aligned bearish structure; 5m close below OR low; failed reclaim and volume confirm.",
    SetupFamily.BREAKOUT_RETEST: "Close above mapped resistance, then hold/retest above it with volume.",
    SetupFamily.BREAKDOWN_RETEST: "Close below mapped support, then failed reclaim beneath it with volume.",
    SetupFamily.VWAP_RECLAIM: "Cross from below VWAP, then hold/retest above VWAP with volume.",
    SetupFamily.VWAP_REJECTION: "Test VWAP from below and fail to reclaim; lower close with volume.",
    SetupFamily.FAILED_BREAKOUT: "Break above resistance, close back below, then fail to reclaim.",
    SetupFamily.FAILED_BREAKDOWN: "Break below support, close back above, then hold the reclaim.",
    SetupFamily.TREND_PULLBACK: "Aligned trend pulls to EMA9/structure, holds, then resumes with volume.",
    SetupFamily.MOMENTUM_CONTINUATION: "Aligned 5m/15m trend pauses and closes through the pause with volume.",
    SetupFamily.COMPRESSION_BREAK: "Narrow multi-bar range breaks, then accepts outside the range with volume.",
}


@dataclass(frozen=True)
class CandidateEvidence:
    family: SetupFamily
    direction: str
    state: SetupProgress
    trigger: float
    reasoning: tuple[str, ...]
    missing: tuple[str, ...]
    # Research provenance only. These fields expose facts already used by the
    # detector; they do not participate in setup selection or state logic.
    anchor_type: str
    anchor: dict[str, object]
    qualifying_bar_at: datetime | None = None


def completed_five_minute_bars(bars: list[Bar]) -> list[Bar]:
    sampled = resample(bars, 5)
    if bars and bars[-1].timestamp.astimezone(ET).minute % 5 != 4:
        sampled = sampled[:-1]
    return sampled


def _mapped(levels: list[PriceLevel], kind: str) -> float | None:
    return next((x.price for x in levels if kind in x.types), None)


def _structure_targets(levels: list[PriceLevel], trigger: float, direction: str):
    structural = sorted({x.price for x in levels if "CURRENT_PRICE" not in x.types})
    if direction == "LONG":
        invalidation = max((x for x in structural if x < trigger), default=None)
        targets = [x for x in structural if x > trigger]
    else:
        invalidation = min((x for x in structural if x > trigger), default=None)
        targets = sorted((x for x in structural if x < trigger), reverse=True)
    return invalidation, (targets + [None, None])[:2]


def _break_candidate(family: SetupFamily, direction: str, level: float, bars: list[Bar],
                     atr: float, rvol: float | None, p: EngineParameters, *,
                     anchor_type: str, anchor: dict[str, object]) -> CandidateEvidence:
    previous, latest = bars[-2], bars[-1]
    buffer, tolerance = atr*p.breakout_buffer_atr, atr*p.retest_tolerance_atr
    volume_ok = rvol is not None and rvol >= p.volume_confirmation_rvol
    if direction == "LONG":
        crossed = latest.close > level + buffer
        accepted = previous.close > level + buffer and latest.low <= level+tolerance and latest.close > level+buffer
        near = abs(latest.close-level) <= atr*p.trigger_proximity_atr
    else:
        crossed = latest.close < level - buffer
        accepted = previous.close < level-buffer and latest.high >= level-tolerance and latest.close < level-buffer
        near = abs(latest.close-level) <= atr*p.trigger_proximity_atr
    state = SetupProgress.CONFIRMED if accepted and volume_ok else SetupProgress.TRIGGERED if crossed else SetupProgress.WATCHING if near else SetupProgress.FORMING
    missing = () if state == SetupProgress.CONFIRMED else (("volume confirmation",) if accepted and not volume_ok else ("5m acceptance/retest", "relative-volume confirmation"))
    return CandidateEvidence(family, direction, state, level,
        (f"Mapped trigger {level:.2f}", SETUP_RULES[family]), missing,
        anchor_type, anchor, qualifying_bar_at=latest.timestamp)


def _bar_times(*bars: Bar) -> list[str]:
    return [bar.timestamp.isoformat() for bar in bars]


def _level_anchor(level: PriceLevel) -> dict[str, object]:
    return {"price": level.price, "level_types": sorted(set(level.types))}


def detect_candidates(instrument: InstrumentAnalysis, levels: list[PriceLevel], bars: list[Bar],
                      parameters: EngineParameters = DEFAULT_PARAMETERS) -> list[CandidateEvidence]:
    five = completed_five_minute_bars(bars)
    atr = instrument.atr_5m or 0
    if len(five) < max(4, parameters.compression_lookback_bars+2) or atr <= 0:
        return []
    prior2, previous, latest = five[-3], five[-2], five[-1]
    f5, f15 = instrument.timeframes["5m"], instrument.timeframes["15m"]
    rvol = f5.relative_volume
    volume_ok = rvol is not None and rvol >= parameters.volume_confirmation_rvol
    tolerance = atr*parameters.retest_tolerance_atr
    vwap_tolerance = atr*parameters.vwap_tolerance_atr
    buffer = atr*parameters.breakout_buffer_atr
    candidates: list[CandidateEvidence] = []
    orh, orl, vwap = (_mapped(levels, x) for x in ("OPENING_RANGE_HIGH", "OPENING_RANGE_LOW", "SESSION_VWAP"))
    aligned_long = f5.trend == f15.trend == Trend.BULLISH
    aligned_short = f5.trend == f15.trend == Trend.BEARISH
    if orh is not None and aligned_long:
        candidates.append(_break_candidate(SetupFamily.OPENING_RANGE_BREAKOUT, "LONG", orh, five, atr, rvol, parameters,
            anchor_type="OPENING_RANGE_HIGH", anchor={"price": orh, "role": "OPENING_RANGE_HIGH",
                "opening_range_minutes": parameters.opening_range_minutes}))
    if orl is not None and aligned_short:
        candidates.append(_break_candidate(SetupFamily.OPENING_RANGE_BREAKDOWN, "SHORT", orl, five, atr, rvol, parameters,
            anchor_type="OPENING_RANGE_LOW", anchor={"price": orl, "role": "OPENING_RANGE_LOW",
                "opening_range_minutes": parameters.opening_range_minutes}))
    resistance_level = min((x for x in levels if x.price >= latest.close and "CURRENT_PRICE" not in x.types),
        key=lambda item: item.price, default=None)
    support_level = max((x for x in levels if x.price <= latest.close and "CURRENT_PRICE" not in x.types),
        key=lambda item: item.price, default=None)
    if resistance_level is not None and aligned_long:
        candidates.append(_break_candidate(SetupFamily.BREAKOUT_RETEST, "LONG", resistance_level.price, five, atr, rvol, parameters,
            anchor_type="MAPPED_RESISTANCE", anchor=_level_anchor(resistance_level)))
    if support_level is not None and aligned_short:
        candidates.append(_break_candidate(SetupFamily.BREAKDOWN_RETEST, "SHORT", support_level.price, five, atr, rvol, parameters,
            anchor_type="MAPPED_SUPPORT", anchor=_level_anchor(support_level)))
    if vwap is not None:
        reclaimed = prior2.close < vwap and previous.close > vwap+buffer and latest.low <= vwap+vwap_tolerance and latest.close > vwap+buffer
        crossed_up = previous.close < vwap and latest.close > vwap+buffer
        rejected = prior2.close < vwap and previous.high >= vwap-vwap_tolerance and previous.close < vwap and latest.close < previous.low
        if reclaimed or crossed_up:
            candidates.append(CandidateEvidence(SetupFamily.VWAP_RECLAIM, "LONG",
                SetupProgress.CONFIRMED if reclaimed and volume_ok else SetupProgress.TRIGGERED,
                vwap, (SETUP_RULES[SetupFamily.VWAP_RECLAIM],), () if reclaimed and volume_ok else ("VWAP hold/retest", "relative-volume confirmation"),
                "SESSION_VWAP_RECLAIM", {"role": "SESSION_VWAP", "sequence_bar_times": _bar_times(prior2, previous, latest)},
                qualifying_bar_at=latest.timestamp))
        if rejected:
            candidates.append(CandidateEvidence(SetupFamily.VWAP_REJECTION, "SHORT",
                SetupProgress.CONFIRMED if volume_ok else SetupProgress.TRIGGERED, vwap,
                (SETUP_RULES[SetupFamily.VWAP_REJECTION],), () if volume_ok else ("relative-volume confirmation",),
                "SESSION_VWAP_REJECTION", {"role": "SESSION_VWAP", "sequence_bar_times": _bar_times(prior2, previous, latest)},
                qualifying_bar_at=latest.timestamp))
    for mapped_level in [x for x in levels if "CURRENT_PRICE" not in x.types]:
        level = mapped_level.price
        failed_up = prior2.close > level+buffer and previous.close < level and latest.high >= level-tolerance and latest.close < level
        failed_down = prior2.close < level-buffer and previous.close > level and latest.low <= level+tolerance and latest.close > level
        if failed_up:
            candidates.append(CandidateEvidence(SetupFamily.FAILED_BREAKOUT, "SHORT", SetupProgress.CONFIRMED if volume_ok else SetupProgress.TRIGGERED, level, (SETUP_RULES[SetupFamily.FAILED_BREAKOUT],), () if volume_ok else ("relative-volume confirmation",),
                "FAILED_STRUCTURAL_LEVEL", {**_level_anchor(mapped_level), "sequence_bar_times": _bar_times(prior2, previous, latest)},
                qualifying_bar_at=latest.timestamp))
        if failed_down:
            candidates.append(CandidateEvidence(SetupFamily.FAILED_BREAKDOWN, "LONG", SetupProgress.CONFIRMED if volume_ok else SetupProgress.TRIGGERED, level, (SETUP_RULES[SetupFamily.FAILED_BREAKDOWN],), () if volume_ok else ("relative-volume confirmation",),
                "RECLAIMED_STRUCTURAL_LEVEL", {**_level_anchor(mapped_level), "sequence_bar_times": _bar_times(prior2, previous, latest)},
                qualifying_bar_at=latest.timestamp))
    ema9 = f5.ema_9
    if aligned_long and prior2.low <= ema9+atr*parameters.trend_pullback_tolerance_atr and latest.close > previous.high:
        candidates.append(CandidateEvidence(SetupFamily.TREND_PULLBACK, "LONG", SetupProgress.CONFIRMED if volume_ok else SetupProgress.TRIGGERED, previous.high, (SETUP_RULES[SetupFamily.TREND_PULLBACK],), () if volume_ok else ("relative-volume confirmation",),
            "PULLBACK_RESUMPTION_BAR", {"ema_role": "EMA_9", "pullback_bar_at": prior2.timestamp.isoformat(),
                "reference_bar_at": previous.timestamp.isoformat(), "trigger": previous.high},
                qualifying_bar_at=latest.timestamp))
    if aligned_short and prior2.high >= ema9-atr*parameters.trend_pullback_tolerance_atr and latest.close < previous.low:
        candidates.append(CandidateEvidence(SetupFamily.TREND_PULLBACK, "SHORT", SetupProgress.CONFIRMED if volume_ok else SetupProgress.TRIGGERED, previous.low, (SETUP_RULES[SetupFamily.TREND_PULLBACK],), () if volume_ok else ("relative-volume confirmation",),
            "PULLBACK_RESUMPTION_BAR", {"ema_role": "EMA_9", "pullback_bar_at": prior2.timestamp.isoformat(),
                "reference_bar_at": previous.timestamp.isoformat(), "trigger": previous.low},
                qualifying_bar_at=latest.timestamp))
    pause = previous.high-previous.low <= atr*parameters.momentum_pause_range_atr
    if aligned_long and pause and latest.close > previous.high+buffer:
        candidates.append(CandidateEvidence(SetupFamily.MOMENTUM_CONTINUATION, "LONG", SetupProgress.CONFIRMED if volume_ok else SetupProgress.TRIGGERED, previous.high, (SETUP_RULES[SetupFamily.MOMENTUM_CONTINUATION],), () if volume_ok else ("relative-volume confirmation",),
            "MOMENTUM_PAUSE_BAR", {"pause_bar_at": previous.timestamp.isoformat(), "pause_high": previous.high,
                "pause_low": previous.low, "breakout_buffer_atr": parameters.breakout_buffer_atr},
                qualifying_bar_at=latest.timestamp))
    if aligned_short and pause and latest.close < previous.low-buffer:
        candidates.append(CandidateEvidence(SetupFamily.MOMENTUM_CONTINUATION, "SHORT", SetupProgress.CONFIRMED if volume_ok else SetupProgress.TRIGGERED, previous.low, (SETUP_RULES[SetupFamily.MOMENTUM_CONTINUATION],), () if volume_ok else ("relative-volume confirmation",),
            "MOMENTUM_PAUSE_BAR", {"pause_bar_at": previous.timestamp.isoformat(), "pause_high": previous.high,
                "pause_low": previous.low, "breakout_buffer_atr": parameters.breakout_buffer_atr},
                qualifying_bar_at=latest.timestamp))
    compression = five[-parameters.compression_lookback_bars-2:-2]
    comp_high, comp_low = max(x.high for x in compression), min(x.low for x in compression)
    if comp_high-comp_low <= atr*parameters.compression_range_atr:
        compression_anchor = {"window_start_at": compression[0].timestamp.isoformat(),
            "window_end_at": compression[-1].timestamp.isoformat(), "range_high": comp_high,
            "range_low": comp_low, "acceptance_bar_times": _bar_times(previous, latest)}
        if previous.close > comp_high+buffer and latest.low <= comp_high+tolerance and latest.close > comp_high:
            candidates.append(CandidateEvidence(SetupFamily.COMPRESSION_BREAK, "LONG", SetupProgress.CONFIRMED if volume_ok else SetupProgress.TRIGGERED, comp_high, (SETUP_RULES[SetupFamily.COMPRESSION_BREAK],), () if volume_ok else ("relative-volume confirmation",),
                "COMPRESSION_WINDOW", compression_anchor, qualifying_bar_at=latest.timestamp))
        elif previous.close < comp_low-buffer and latest.high >= comp_low-tolerance and latest.close < comp_low:
            candidates.append(CandidateEvidence(SetupFamily.COMPRESSION_BREAK, "SHORT", SetupProgress.CONFIRMED if volume_ok else SetupProgress.TRIGGERED, comp_low, (SETUP_RULES[SetupFamily.COMPRESSION_BREAK],), () if volume_ok else ("relative-volume confirmation",),
                "COMPRESSION_WINDOW", compression_anchor, qualifying_bar_at=latest.timestamp))
    return candidates


def build_standard_decision(instrument: InstrumentAnalysis, levels: list[PriceLevel], bars: list[Bar],
                            score: int, previous: StandardDecision | None = None,
                            parameters: EngineParameters = DEFAULT_PARAMETERS) -> StandardDecision:
    now = instrument.as_of or datetime.now(timezone.utc)
    price, atr = instrument.timeframes["1m"].price, instrument.atr_5m
    supports = [x.price for x in levels if x.price < price]
    resistances = [x.price for x in levels if x.price > price]
    base = dict(symbol=instrument.symbol, timestamp=now, market_price=price,
        nearest_support=max(supports, default=None), nearest_resistance=min(resistances, default=None),
        vwap_state=instrument.timeframes["5m"].vwap_state, structure_5m=instrument.timeframes["5m"].trend,
        structure_15m=instrument.timeframes["15m"].trend, atr=atr, quality_is_heuristic=True)
    candidates = detect_candidates(instrument, levels, bars, parameters)
    rank = {SetupProgress.CONFIRMED: 5, SetupProgress.TRIGGERED: 4, SetupProgress.WATCHING: 3, SetupProgress.FORMING: 2}
    candidates.sort(key=lambda x: rank.get(x.state, 0), reverse=True)
    if len(candidates) > 1 and rank.get(candidates[0].state, 0) == rank.get(candidates[1].state, 0) and candidates[0].direction != candidates[1].direction:
        candidates = []
    if not candidates or atr is None:
        state = SetupProgress.INVALIDATED if previous and previous.setup_state not in {SetupProgress.NO_SETUP, SetupProgress.INVALIDATED} else SetupProgress.NO_SETUP
        return StandardDecision(**base, direction="NONE", setup_family=previous.setup_family if state == SetupProgress.INVALIDATED else SetupFamily.NONE,
            setup_state=state, quality=DecisionQuality.NO_TRADE, action_now=ActionNow.NO_TRADE,
            reasoning=["No setup family has complete deterministic prerequisites"], missing_confirmation=["recognizable structure and price behavior"],
            do_not_trade_if=["conditions remain ambiguous"], what_changes_my_mind=["new mapped structure and a qualifying setup sequence"],
            prior_state=previous.setup_state if previous else None, state_changed_at=now)
    chosen = candidates[0]
    invalidation, targets = _structure_targets(levels, chosen.trigger, chosen.direction)
    state = chosen.state
    rr = None
    if invalidation is not None and targets[0] is not None:
        risk = abs(chosen.trigger-invalidation)
        rr = abs(targets[0]-chosen.trigger)/risk if risk else None
    beyond = (price-chosen.trigger if chosen.direction == "LONG" else chosen.trigger-price)
    if state in {SetupProgress.TRIGGERED, SetupProgress.CONFIRMED} and beyond > atr*parameters.chase_threshold_atr:
        state = SetupProgress.TOO_LATE
    if rr is not None and rr < parameters.minimum_structural_risk_reward:
        state = SetupProgress.TOO_LATE if chosen.state in {SetupProgress.TRIGGERED, SetupProgress.CONFIRMED} else SetupProgress.FORMING
    target_space = abs((targets[0] or chosen.trigger)-chosen.trigger)
    if state in {SetupProgress.TRIGGERED, SetupProgress.CONFIRMED} and target_space <= atr*parameters.opposing_level_buffer_atr:
        state = SetupProgress.TOO_LATE
    if previous and previous.setup_family == chosen.family and previous.invalidation is not None:
        breached = price < previous.invalidation if previous.direction == "LONG" else price > previous.invalidation
        if breached:
            state = SetupProgress.INVALIDATED
    complete_structure = invalidation is not None and targets[0] is not None and targets[1] is not None
    quality = DecisionQuality.NO_TRADE if state in {SetupProgress.INVALIDATED, SetupProgress.TOO_LATE} or not complete_structure else DecisionQuality.A if score >= parameters.quality_a_score and state in {SetupProgress.CONFIRMED, SetupProgress.WATCHING} else DecisionQuality.B if score >= parameters.quality_b_score else DecisionQuality.C
    action = ActionNow.NO_TRADE
    if state == SetupProgress.TOO_LATE: action = ActionNow.DO_NOT_CHASE
    elif state == SetupProgress.CONFIRMED: action = ActionNow.LONG_TRIGGERED if chosen.direction == "LONG" else ActionNow.SHORT_TRIGGERED
    elif state in {SetupProgress.WATCHING, SetupProgress.TRIGGERED}: action = ActionNow.PREPARE_LONG if chosen.direction == "LONG" else ActionNow.PREPARE_SHORT
    elif state == SetupProgress.FORMING: action = ActionNow.WAIT
    missing = list(chosen.missing)
    if not complete_structure: missing.append("two structural targets and a mapped invalidation")
    changes = ["WAIT FOR RETEST", "WAIT FOR NEXT LEVEL", "WAIT FOR NEW STRUCTURE"] if state == SetupProgress.TOO_LATE else [f"Completion or failure of {chosen.family.value} confirmation sequence"]
    changed_at = previous.state_changed_at if previous and previous.setup_family == chosen.family and previous.setup_state == state else now
    return StandardDecision(**base, direction=chosen.direction, setup_family=chosen.family, setup_state=state,
        quality=quality, action_now=action, trigger=chosen.trigger, invalidation=invalidation,
        target_1=targets[0], target_2=targets[1], structural_risk_reward=rr,
        reasoning=list(chosen.reasoning), missing_confirmation=missing,
        do_not_trade_if=["touch without close/acceptance", "invalidation breaks", "entry becomes extended", "target 1 is blocked by opposing structure"],
        what_changes_my_mind=changes, prior_state=previous.setup_state if previous else None,
        state_changed_at=changed_at)
