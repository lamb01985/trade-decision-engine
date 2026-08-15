from app.models import (
    ConfirmationStatus, CrossMarketAnalysis, InstrumentAnalysis, SetupDecision, SetupState,
    TradePlan, Trend,
)
from app.models import PriceLevel, ScoreFactor, SetupScore
from app.parameters import DEFAULT_PARAMETERS


def cross_market_confirmation(qqq: InstrumentAnalysis, nq: InstrumentAnalysis) -> CrossMarketAnalysis:
    q5, q15 = qqq.timeframes["5m"], qqq.timeframes["15m"]
    if not nq.available:
        if q5.trend == q15.trend == Trend.BULLISH:
            status = ConfirmationStatus.PARTIAL_BULLISH_CONFIRMATION
        elif q5.trend == q15.trend == Trend.BEARISH:
            status = ConfirmationStatus.PARTIAL_BEARISH_CONFIRMATION
        else:
            status = ConfirmationStatus.NEUTRAL
        return CrossMarketAnalysis(
            qqq_vwap_state=q5.vwap_state, nq_vwap_state="UNAVAILABLE", qqq_5m_trend=q5.trend,
            nq_5m_trend=None, qqq_15m_trend=q15.trend, nq_15m_trend=None,
            confirmation_status=status,
            explanation="NQ is unavailable; status reflects QQQ alignment only and is capped at partial confirmation.",
        )
    n5, n15 = nq.timeframes["5m"], nq.timeframes["15m"]
    all_trends = [q5.trend, q15.trend, n5.trend, n15.trend]
    if all(t == Trend.BULLISH for t in all_trends) and q5.vwap_state == n5.vwap_state == "ABOVE":
        status = ConfirmationStatus.STRONG_BULLISH_CONFIRMATION
    elif all(t == Trend.BEARISH for t in all_trends) and q5.vwap_state == n5.vwap_state == "BELOW":
        status = ConfirmationStatus.STRONG_BEARISH_CONFIRMATION
    elif sum(t == Trend.BULLISH for t in all_trends) >= 3:
        status = ConfirmationStatus.PARTIAL_BULLISH_CONFIRMATION
    elif sum(t == Trend.BEARISH for t in all_trends) >= 3:
        status = ConfirmationStatus.PARTIAL_BEARISH_CONFIRMATION
    else:
        status = ConfirmationStatus.NEUTRAL
    return CrossMarketAnalysis(
        qqq_vwap_state=q5.vwap_state, nq_vwap_state=n5.vwap_state, qqq_5m_trend=q5.trend,
        nq_5m_trend=n5.trend, qqq_15m_trend=q15.trend, nq_15m_trend=n15.trend,
        confirmation_status=status, explanation="QQQ and NQ were evaluated independently across VWAP, 5m, and 15m structure.",
    )


def evaluate_setup(qqq: InstrumentAnalysis, cross: CrossMarketAnalysis) -> SetupDecision:
    f5, f15 = qqq.timeframes["5m"], qqq.timeframes["15m"]
    price = f5.price
    long_levels = [x for x in (qqq.opening_range_high, qqq.prior_day_high) if x is not None]
    short_levels = [x for x in (qqq.opening_range_low, qqq.prior_day_low) if x is not None]
    breakout = max(long_levels) if long_levels else None
    breakdown = min(short_levels) if short_levels else None
    volume_ok = f5.relative_volume is not None and f5.relative_volume >= DEFAULT_PARAMETERS.volume_confirmation_rvol
    bullish = f5.trend == f15.trend == Trend.BULLISH and f5.vwap_state == "ABOVE"
    bearish = f5.trend == f15.trend == Trend.BEARISH and f5.vwap_state == "BELOW"
    cross_bull = cross.confirmation_status in {ConfirmationStatus.PARTIAL_BULLISH_CONFIRMATION, ConfirmationStatus.STRONG_BULLISH_CONFIRMATION}
    cross_bear = cross.confirmation_status in {ConfirmationStatus.PARTIAL_BEARISH_CONFIRMATION, ConfirmationStatus.STRONG_BEARISH_CONFIRMATION}
    reasons = [f"QQQ 5m/15m: {f5.trend.value}/{f15.trend.value}", f"Price is {f5.vwap_state.lower()} VWAP"]
    if bullish and cross_bull:
        if breakout is not None and price > breakout and volume_ok:
            return SetupDecision(state=SetupState.LONG_CONFIRMED, explanation=reasons + ["Meaningful resistance broke with relative-volume confirmation"], breakout_level=breakout, breakdown_level=breakdown, volume_confirmed=True)
        return SetupDecision(state=SetupState.WATCH_LONG, explanation=reasons + ["Bullish thesis exists, but breakout and/or volume confirmation is incomplete"], breakout_level=breakout, breakdown_level=breakdown, volume_confirmed=volume_ok)
    if bearish and cross_bear:
        if breakdown is not None and price < breakdown and volume_ok:
            return SetupDecision(state=SetupState.SHORT_CONFIRMED, explanation=reasons + ["Meaningful support broke with relative-volume confirmation"], breakout_level=breakout, breakdown_level=breakdown, volume_confirmed=True)
        return SetupDecision(state=SetupState.WATCH_SHORT, explanation=reasons + ["Bearish thesis exists, but breakdown and/or volume confirmation is incomplete"], breakout_level=breakout, breakdown_level=breakdown, volume_confirmed=volume_ok)
    return SetupDecision(state=SetupState.NO_TRADE, explanation=reasons + ["Multi-timeframe structure is not aligned"], breakout_level=breakout, breakdown_level=breakdown, volume_confirmed=volume_ok)


def build_trade_plan(qqq: InstrumentAnalysis, setup: SetupDecision) -> TradePlan:
    if setup.state not in {SetupState.LONG_CONFIRMED, SetupState.SHORT_CONFIRMED}:
        return TradePlan(status="INSUFFICIENT_CONFIRMATION", explanation="No plan: thesis has not become a confirmed setup.")
    price = qqq.timeframes["5m"].price
    if setup.state == SetupState.LONG_CONFIRMED:
        trigger, invalidation, direction = setup.breakout_level, qqq.opening_range_low, "LONG"
        risk = trigger - invalidation if trigger is not None and invalidation is not None else 0
        targets = (trigger + risk, trigger + 2*risk) if trigger is not None else (None, None)
    else:
        trigger, invalidation, direction = setup.breakdown_level, qqq.opening_range_high, "SHORT"
        risk = invalidation - trigger if trigger is not None and invalidation is not None else 0
        targets = (trigger - risk, trigger - 2*risk) if trigger is not None else (None, None)
    if risk <= 0 or trigger is None or price is None:
        return TradePlan(status="INSUFFICIENT_CONFIRMATION", explanation="Market structure does not provide a valid trigger/invalidation pair.")
    return TradePlan(direction=direction, trigger=trigger, invalidation=invalidation, target_1=targets[0], target_2=targets[1], risk_reward=2.0, status="CONFIRMED", explanation="Levels derive from the opening range and prior-session structure; this is decision support, not an order.")


def state_changes(qqq: InstrumentAnalysis, setup: SetupDecision) -> list[str]:
    changes = []
    if setup.breakdown_level is not None:
        changes.append(f"QQQ must lose {setup.breakdown_level:.2f} and remain below it with aligned 5m/15m bearish structure and volume for short confirmation.")
    if setup.breakout_level is not None:
        changes.append(f"QQQ must reclaim {setup.breakout_level:.2f} and hold above it with aligned 5m/15m bullish structure and volume for long confirmation.")
    return changes or ["More completed market structure is required before a confirmation level can be calculated."]


def evaluate_symbol_setup(instrument: InstrumentAnalysis, levels: list[PriceLevel]) -> SetupDecision:
    f5, f15 = instrument.timeframes["5m"], instrument.timeframes["15m"]
    price = f5.price
    resistance = [level.price for level in levels if level.price >= price and "CURRENT_PRICE" not in level.types]
    support = [level.price for level in levels if level.price <= price and "CURRENT_PRICE" not in level.types]
    trigger_long = min(resistance, default=None)
    trigger_short = max(support, default=None)
    rvol_ok = f5.relative_volume is not None and f5.relative_volume >= DEFAULT_PARAMETERS.volume_confirmation_rvol
    bullish = f5.trend == f15.trend == Trend.BULLISH and f5.vwap_state == "ABOVE"
    bearish = f5.trend == f15.trend == Trend.BEARISH and f5.vwap_state == "BELOW"
    reasons = [f"{instrument.symbol} 5m/15m: {f5.trend.value}/{f15.trend.value}", f"Price is {f5.vwap_state.lower()} session VWAP"]
    # Confirmation requires a completed break of meaningful session/prior structure, never score alone.
    long_breaks = [x for x in (instrument.opening_range_high, instrument.prior_day_high) if x is not None]
    short_breaks = [x for x in (instrument.opening_range_low, instrument.prior_day_low) if x is not None]
    breakout = max(long_breaks) if long_breaks else trigger_long
    breakdown = min(short_breaks) if short_breaks else trigger_short
    if bullish:
        confirmed = breakout is not None and price > breakout and rvol_ok
        setup_type = "BREAKOUT" if confirmed else "TREND_PULLBACK" if instrument.atr_5m and abs(price-f5.ema_9) < instrument.atr_5m*DEFAULT_PARAMETERS.trend_pullback_tolerance_atr else "RANGE_BREAKOUT"
        return SetupDecision(state=SetupState.LONG_CONFIRMED if confirmed else SetupState.WATCH_LONG,
            explanation=reasons + (["Trigger occurred with relative-volume confirmation"] if confirmed else ["Bullish structure exists; trigger has not yet met every confirmation rule"]),
            breakout_level=breakout, breakdown_level=breakdown, volume_confirmed=rvol_ok, setup_type=setup_type, trigger_occurred=confirmed)
    if bearish:
        confirmed = breakdown is not None and price < breakdown and rvol_ok
        setup_type = "BREAKDOWN" if confirmed else "TREND_PULLBACK" if instrument.atr_5m and abs(price-f5.ema_9) < instrument.atr_5m*DEFAULT_PARAMETERS.trend_pullback_tolerance_atr else "RANGE_BREAKDOWN"
        return SetupDecision(state=SetupState.SHORT_CONFIRMED if confirmed else SetupState.WATCH_SHORT,
            explanation=reasons + (["Trigger occurred with relative-volume confirmation"] if confirmed else ["Bearish structure exists; trigger has not yet met every confirmation rule"]),
            breakout_level=breakout, breakdown_level=breakdown, volume_confirmed=rvol_ok, setup_type=setup_type, trigger_occurred=confirmed)
    return SetupDecision(state=SetupState.NO_TRADE, explanation=reasons + ["Multi-timeframe structure is not aligned"],
        breakout_level=breakout, breakdown_level=breakdown, volume_confirmed=rvol_ok, setup_type="COMPRESSION" if f5.ema_alignment == "FLAT" else "NO_SETUP")


def score_setup(instrument: InstrumentAnalysis, setup: SetupDecision, levels: list[PriceLevel], plan: TradePlan | None = None) -> SetupScore:
    f1, f5, f15 = (instrument.timeframes[x] for x in ("1m", "5m", "15m"))
    directional = Trend.BULLISH if setup.state in {SetupState.WATCH_LONG, SetupState.LONG_CONFIRMED} else Trend.BEARISH if setup.state in {SetupState.WATCH_SHORT, SetupState.SHORT_CONFIRMED} else Trend.NEUTRAL
    aligned5 = 15 if directional != Trend.NEUTRAL and f5.trend == directional else 0
    aligned15 = 15 if directional != Trend.NEUTRAL and f15.trend == directional else 0
    vwap = 15 if (directional == Trend.BULLISH and f5.vwap_state == "ABOVE") or (directional == Trend.BEARISH and f5.vwap_state == "BELOW") else 0
    ema_points = 10 if f5.ema_alignment == directional.value else 0
    rvol = min(15, round(max(0, (f5.relative_volume or 0) - DEFAULT_PARAMETERS.score_rvol_baseline) * 15))
    nearby = any(abs(level.distance_pct) <= DEFAULT_PARAMETERS.score_level_proximity_pct and level.confluence >= 2 for level in levels)
    confirmation = 15 if setup.trigger_occurred else 0
    higher = 10 if f1.trend == f5.trend == f15.trend and directional != Trend.NEUTRAL else 0
    rr = 5 if plan and plan.risk_reward and plan.risk_reward >= DEFAULT_PARAMETERS.minimum_structural_risk_reward else 0
    values = [("5m trend alignment", aligned5, 15), ("15m trend alignment", aligned15, 15),
              ("VWAP alignment", vwap, 15), ("EMA 9/21 alignment", ema_points, 10),
              ("relative volume", rvol, 15), ("meaningful level proximity", 10 if nearby else 0, 10),
              ("trigger confirmation", confirmation, 15), ("higher-timeframe agreement", higher, 10),
              ("risk/reward quality", rr, 5)]
    factors = [ScoreFactor(name=n, points=p, max_points=m, explanation=f"{p}/{m} deterministic points") for n, p, m in values]
    # Component maxima total 110; normalize transparently to 100.
    return SetupScore(score=round(sum(x[1] for x in values) / 110 * 100), factors=factors)


def build_symbol_trade_plan(instrument: InstrumentAnalysis, setup: SetupDecision, levels: list[PriceLevel]) -> TradePlan:
    price = instrument.timeframes["5m"].price
    long_side = setup.state in {SetupState.WATCH_LONG, SetupState.LONG_CONFIRMED}
    short_side = setup.state in {SetupState.WATCH_SHORT, SetupState.SHORT_CONFIRMED}
    if not (long_side or short_side):
        return TradePlan(status="NO_SETUP", explanation="No aligned structure provides a responsible trade plan.")
    supports = sorted((x.price for x in levels if x.price < price), reverse=True)
    resistances = sorted(x.price for x in levels if x.price > price)
    trigger = setup.breakout_level if long_side else setup.breakdown_level
    invalidation = (supports[0] if supports else None) if long_side else (resistances[0] if resistances else None)
    if trigger is None or invalidation is None:
        return TradePlan(direction="LONG" if long_side else "SHORT", trigger=trigger, status="WATCH",
            explanation="A trigger is identified, but structure does not provide a valid invalidation/target pair.")
    risk = abs(trigger - invalidation)
    if risk <= 0:
        return TradePlan(status="WATCH", explanation="Trigger and invalidation are too close for a valid plan.")
    sign = 1 if long_side else -1
    confirmed = setup.trigger_occurred
    return TradePlan(direction="LONG" if long_side else "SHORT", trigger=trigger, invalidation=invalidation,
        target_1=trigger + sign*risk, target_2=trigger + sign*2*risk, risk_reward=2.0,
        status="CONFIRMED" if confirmed else "AWAITING_TRIGGER",
        explanation="Structure-derived plan. A setup is not confirmed until its trigger occurs.")
