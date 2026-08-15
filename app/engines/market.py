from collections import defaultdict
from datetime import time
from statistics import mean
from zoneinfo import ZoneInfo

from app.engines.indicators import average_true_range, ema, session_vwap
from app.parameters import DEFAULT_PARAMETERS
from app.models import Bar, InstrumentAnalysis, TimeframeAnalysis, Trend

ET = ZoneInfo("America/New_York")


def classify_trend(price: float, vwap: float, ema_9: float, ema_21: float) -> Trend:
    if price > vwap and ema_9 > ema_21 and price > ema_9 and price > ema_21:
        return Trend.BULLISH
    if price < vwap and ema_9 < ema_21 and price < ema_9 and price < ema_21:
        return Trend.BEARISH
    return Trend.NEUTRAL


def regular_session_bars(bars: list[Bar]) -> list[Bar]:
    return [b for b in bars if time(9, 30) <= b.timestamp.astimezone(ET).time() < time(16, 0)]


def resample(bars: list[Bar], minutes: int) -> list[Bar]:
    groups: dict[tuple, list[Bar]] = defaultdict(list)
    for bar in bars:
        local = bar.timestamp.astimezone(ET)
        bucket_minute = (local.minute // minutes) * minutes
        groups[(local.date(), local.hour, bucket_minute)].append(bar)
    result = []
    for group in groups.values():
        result.append(Bar(timestamp=group[0].timestamp, open=group[0].open,
                          high=max(b.high for b in group), low=min(b.low for b in group),
                          close=group[-1].close, volume=sum(b.volume for b in group)))
    return sorted(result, key=lambda b: b.timestamp)


def analyze_symbol(symbol: str, bars: list[Bar]) -> InstrumentAnalysis:
    symbol = symbol.upper()
    regular = regular_session_bars(bars)
    if not regular:
        return InstrumentAnalysis(symbol=symbol, available=False, message=f"No regular-session {symbol} bars available")
    by_day: dict[object, list[Bar]] = defaultdict(list)
    for bar in regular:
        by_day[bar.timestamp.astimezone(ET).date()].append(bar)
    dates = sorted(by_day)
    current = by_day[dates[-1]]
    prior = by_day[dates[-2]] if len(dates) > 1 else None
    current_date = dates[-1]
    premarket = [b for b in bars if b.timestamp.astimezone(ET).date() == current_date and time(4, 0) <= b.timestamp.astimezone(ET).time() < time(9, 30)]
    opening_minutes = 9*60 + 30 + DEFAULT_PARAMETERS.opening_range_minutes
    opening_cutoff = time(opening_minutes//60, opening_minutes%60)
    opening = [b for b in current if b.timestamp.astimezone(ET).time() < opening_cutoff]
    frames = {}
    for label, minutes in (("1m", 1), ("5m", 5), ("15m", 15)):
        sampled = resample(current, minutes)
        closes = [b.close for b in sampled]
        e9, e21 = ema(closes, 9)[-1], ema(closes, 21)[-1]
        price = closes[-1]
        vwap = session_vwap(current)
        completed_volumes = [b.volume for b in sampled[:-1]][-20:]
        rvol = sampled[-1].volume / mean(completed_volumes) if completed_volumes and mean(completed_volumes) else None
        frames[label] = TimeframeAnalysis(
            timeframe=label, price=price, vwap=vwap, ema_9=e9, ema_21=e21,
            vwap_state="ABOVE" if price > vwap else "BELOW" if price < vwap else "AT",
            ema_alignment="BULLISH" if e9 > e21 else "BEARISH" if e9 < e21 else "FLAT",
            distance_from_vwap=price-vwap, distance_from_vwap_pct=(price-vwap)/vwap*100,
            trend=classify_trend(price, vwap, e9, e21), volume=sampled[-1].volume,
            relative_volume=rvol,
        )
    return InstrumentAnalysis(
        symbol=symbol, available=True, as_of=current[-1].timestamp, timeframes=frames,
        high_of_day=max(b.high for b in current), low_of_day=min(b.low for b in current),
        prior_day_high=max(b.high for b in prior) if prior else None,
        prior_day_low=min(b.low for b in prior) if prior else None,
        prior_day_close=prior[-1].close if prior else None,
        opening_range_high=max((b.high for b in opening), default=None),
        opening_range_low=min((b.low for b in opening), default=None),
        premarket_high=max((b.high for b in premarket), default=None),
        premarket_low=min((b.low for b in premarket), default=None),
        change_pct=((current[-1].close - prior[-1].close) / prior[-1].close * 100) if prior else None,
        average_dollar_volume=mean(
            sum(b.volume * b.close for b in by_day[day]) for day in dates[-5:]
        ),
        atr_5m=average_true_range(resample(current, 5), DEFAULT_PARAMETERS.atr_period),
    )


def analyze_qqq(bars: list[Bar]) -> InstrumentAnalysis:
    """Backward-compatible V1 entry point."""
    return analyze_symbol("QQQ", bars)


def unavailable_nq() -> InstrumentAnalysis:
    return InstrumentAnalysis(
        symbol="NQ", available=False,
        message="Data unavailable / pending futures integration. No QQQ-derived values are inferred for NQ.",
    )
