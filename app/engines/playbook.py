from app.engines.market import ET, resample
from app.models import Bar, LevelActionPlan, LevelActionStatus, PriceLevel
from app.parameters import DEFAULT_PARAMETERS, EngineParameters


def _targets(level: PriceLevel, levels: list[PriceLevel]) -> tuple[float | None, float | None, float | None, float | None]:
    above = sorted({x.price for x in levels if x.price > level.price and "CURRENT_PRICE" not in x.types})
    below = sorted({x.price for x in levels if x.price < level.price and "CURRENT_PRICE" not in x.types}, reverse=True)
    values = (above + [None, None])[:2] + (below + [None, None])[:2]
    return values[0], values[1], values[2], values[3]


def _behavior(level: PriceLevel, bars: list[Bar], rvol: float | None, atr: float | None = None,
              parameters: EngineParameters = DEFAULT_PARAMETERS) -> tuple[LevelActionStatus, float | None, str]:
    sampled = resample(bars, 5)
    if bars and bars[-1].timestamp.astimezone(ET).minute % 5 != 4:
        sampled = sampled[:-1]
    if len(sampled) < 3:
        return LevelActionStatus.WAIT, None, "Not enough completed 5m behavior"
    previous, latest = sampled[-2], sampled[-1]
    price = level.price
    tolerance = (atr or level.price*.01) * parameters.retest_tolerance_atr
    volume_ok = rvol is not None and rvol >= parameters.volume_confirmation_rvol
    # Confirmation is deliberately two-step: a close across structure followed by a hold/retest.
    bullish_retest = previous.close > price and latest.low <= price + tolerance and latest.close > price and volume_ok
    bearish_retest = previous.close < price and latest.high >= price - tolerance and latest.close < price and volume_ok
    bullish_reclaim = previous.close < price and latest.close > price and latest.low <= price + tolerance
    bearish_break = previous.close > price and latest.close < price and latest.high >= price - tolerance
    rejection = latest.high >= price - tolerance and latest.close < price and latest.close < latest.open
    support_hold = latest.low <= price + tolerance and latest.close > price and latest.close > latest.open
    if bullish_retest:
        return LevelActionStatus.LONG_CONFIRMED, latest.close, "5m break, close, and volume-backed hold/retest confirmed"
    if bearish_retest:
        return LevelActionStatus.SHORT_CONFIRMED, latest.close, "5m break, close, and volume-backed failed reclaim confirmed"
    if bullish_reclaim:
        return LevelActionStatus.LEVEL_RECLAIMED, None, "Level reclaimed; wait for a successful hold/retest"
    if bearish_break:
        return LevelActionStatus.LEVEL_BROKEN, None, "Level broken; wait for a failed reclaim from underneath"
    if rejection:
        return LevelActionStatus.SHORT_SETUP_FORMING, None, "Rejection is forming; require a lower high or failed reclaim"
    if support_hold:
        return LevelActionStatus.LONG_SETUP_FORMING, None, "Hold is forming; require a higher low or confirmed reclaim"
    distance = abs(latest.close-price)
    unit = atr or price*.01
    if distance <= unit*parameters.level_testing_atr:
        return LevelActionStatus.TESTING_LEVEL, None, "Price is testing the level; touching is not an entry"
    if distance <= unit*parameters.level_approaching_atr:
        return LevelActionStatus.APPROACHING_LEVEL, None, "Price is approaching; wait for behavior at the level"
    return LevelActionStatus.WAIT, None, "Price is not currently interacting with this level"


def build_level_playbooks(levels: list[PriceLevel], bars: list[Bar], current_price: float,
                          relative_volume: float | None, atr: float | None = None,
                          parameters: EngineParameters = DEFAULT_PARAMETERS) -> list[LevelActionPlan]:
    structural = [x for x in levels if "CURRENT_PRICE" not in x.types]
    plans = []
    for level in structural:
        lt1, lt2, st1, st2 = _targets(level, structural)
        status, entry, behavior = _behavior(level, bars, relative_volume, atr, parameters)
        long_valid = lt1 is not None and lt2 is not None
        short_valid = st1 is not None and st2 is not None
        if status == LevelActionStatus.LONG_CONFIRMED and (not long_valid or lt1 <= entry):
            status, entry = LevelActionStatus.WAIT, None
            behavior = "Bullish behavior detected, but targets are insufficient; no confirmed plan"
        if status == LevelActionStatus.SHORT_CONFIRMED and (not short_valid or st1 >= entry):
            status, entry = LevelActionStatus.WAIT, None
            behavior = "Bearish behavior detected, but targets are insufficient; no confirmed plan"
        rr = None
        if entry is not None:
            target = lt1 if status == LevelActionStatus.LONG_CONFIRMED else st1
            risk = abs(entry - level.price)
            if target is not None and risk > 0:
                rr = abs(target - entry) / risk
        plans.append(LevelActionPlan(
            level_price=level.price, level_type=level.types, importance=level.importance,
            confluence=level.confluence, current_price_distance=level.price-current_price,
            current_price_distance_pct=(level.price-current_price)/current_price*100,
            bullish_scenario=f"Wait for a 5m close above {level.price:.2f}, then a successful hold/retest with volume confirmation.",
            bearish_scenario=f"Wait for rejection or a 5m close below {level.price:.2f}, then a lower high or failed reclaim.",
            long_trigger=f"5m close above {level.price:.2f} plus successful hold/retest; touch alone is invalid.",
            short_trigger=f"5m close below {level.price:.2f} plus rejection/failed reclaim; touch alone is invalid.",
            long_invalidation=f"5m close back below mapped level {level.price:.2f} after the retest.",
            short_invalidation=f"5m reclaim above mapped level {level.price:.2f} after the failed reclaim.",
            long_target_1=lt1, long_target_2=lt2, short_target_1=st1, short_target_2=st2,
            status=status, entry=entry, risk_reward=rr,
            structure_status=(behavior + ("; long targets insufficient" if not long_valid else "") +
                              ("; short targets insufficient" if not short_valid else ""))))
    return plans
