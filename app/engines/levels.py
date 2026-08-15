from collections import defaultdict
from dataclasses import dataclass

from app.engines.indicators import ema, session_vwap
from app.engines.market import resample
from app.models import Bar, InstrumentAnalysis, LevelImportance, PriceLevel
from app.parameters import DEFAULT_PARAMETERS, EngineParameters


@dataclass(frozen=True)
class RawLevel:
    price: float
    kind: str


def swing_levels(bars: list[Bar], minutes: int, window: int | None = None,
                 parameters: EngineParameters = DEFAULT_PARAMETERS) -> list[RawLevel]:
    window = window or parameters.swing_window_bars
    sampled = resample(bars, minutes)[-parameters.structure_lookback_bars:]
    found: list[RawLevel] = []
    for index in range(window, len(sampled) - window):
        bar = sampled[index]
        neighbors = sampled[index-window:index] + sampled[index+1:index+window+1]
        if all(bar.high > other.high for other in neighbors):
            found.append(RawLevel(bar.high, f"{minutes}M_SWING_HIGH"))
        if all(bar.low < other.low for other in neighbors):
            found.append(RawLevel(bar.low, f"{minutes}M_SWING_LOW"))
    return found[-parameters.maximum_swings_per_timeframe:]


def deduplicate_levels(raw: list[RawLevel], current_price: float, threshold_pct: float | None = None,
                       atr: float | None = None, parameters: EngineParameters = DEFAULT_PARAMETERS) -> list[tuple[float, list[str]]]:
    if not raw:
        return []
    tolerance = (current_price * threshold_pct / 100) if threshold_pct is not None else max(
        current_price * parameters.level_merge_minimum_pct / 100,
        (atr or 0) * parameters.level_merge_atr_fraction,
    )
    groups: list[list[RawLevel]] = []
    for item in sorted(raw, key=lambda x: x.price):
        if groups and abs(item.price - sum(x.price for x in groups[-1]) / len(groups[-1])) <= tolerance:
            groups[-1].append(item)
        else:
            groups.append([item])
    return [
        (next((x.price for x in group if x.kind == "CURRENT_PRICE"), sum(x.price for x in group) / len(group)),
         list(dict.fromkeys(x.kind for x in group)))
        for group in groups
    ]


def _importance(price: float, current: float, types: list[str]) -> LevelImportance:
    major = len(types) >= 2 or any(t in {"HOD", "LOD", "PRIOR_DAY_HIGH", "PRIOR_DAY_LOW", "GAP_UP", "GAP_DOWN"} for t in types)
    if "CURRENT_PRICE" in types:
        return LevelImportance.PIVOT
    if price > current:
        return LevelImportance.MAJOR_RESISTANCE if major else LevelImportance.RESISTANCE
    return LevelImportance.MAJOR_SUPPORT if major else LevelImportance.SUPPORT


def build_levels(instrument: InstrumentAnalysis, current_bars: list[Bar],
                 parameters: EngineParameters = DEFAULT_PARAMETERS) -> list[PriceLevel]:
    current = instrument.timeframes["1m"].price
    f1 = instrument.timeframes["1m"]
    raw = [RawLevel(current, "CURRENT_PRICE"), RawLevel(f1.vwap, "SESSION_VWAP"),
           RawLevel(f1.ema_9, "EMA_9"), RawLevel(f1.ema_21, "EMA_21")]
    named = {
        "HOD": instrument.high_of_day, "LOD": instrument.low_of_day,
        "PRIOR_DAY_HIGH": instrument.prior_day_high, "PRIOR_DAY_LOW": instrument.prior_day_low,
        "PRIOR_DAY_CLOSE": instrument.prior_day_close, "PREMARKET_HIGH": instrument.premarket_high,
        "PREMARKET_LOW": instrument.premarket_low, "OPENING_RANGE_HIGH": instrument.opening_range_high,
        "OPENING_RANGE_LOW": instrument.opening_range_low,
    }
    raw.extend(RawLevel(value, kind) for kind, value in named.items() if value is not None)
    raw.extend(swing_levels(current_bars, 5, parameters=parameters))
    raw.extend(swing_levels(current_bars, 15, parameters=parameters))
    if instrument.prior_day_close is not None and current_bars:
        session_open = current_bars[0].open
        if session_open > instrument.prior_day_close * (1 + parameters.gap_detection_pct/100):
            raw.append(RawLevel(instrument.prior_day_close, "GAP_UP"))
        elif session_open < instrument.prior_day_close * (1 - parameters.gap_detection_pct/100):
            raw.append(RawLevel(instrument.prior_day_close, "GAP_DOWN"))
    levels = []
    for price, types in deduplicate_levels(raw, current, atr=instrument.atr_5m, parameters=parameters):
        distance = price - current
        levels.append(PriceLevel(price=price, types=types, importance=_importance(price, current, types),
            distance=distance, distance_pct=distance/current*100, position="ABOVE" if distance > 0 else "BELOW" if distance < 0 else "AT", confluence=len(types)))
    return sorted(levels, key=lambda level: level.price, reverse=True)
