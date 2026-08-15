from datetime import datetime, timezone

from app.models import InstrumentAnalysis, TimeframeAnalysis, Trend


def frame(label: str, trend: Trend, price: float, vwap_state: str, rvol: float = 1.3):
    return TimeframeAnalysis(timeframe=label, price=price, vwap=100, ema_9=101, ema_21=100,
        vwap_state=vwap_state, ema_alignment=trend.value, distance_from_vwap=price-100,
        distance_from_vwap_pct=price-100, trend=trend, volume=1000, relative_volume=rvol)


def instrument(trend=Trend.BULLISH, price=111, vwap_state="ABOVE", available=True, rvol=1.3):
    return InstrumentAnalysis(symbol="QQQ", available=available, as_of=datetime.now(timezone.utc),
        timeframes={k: frame(k, trend, price, vwap_state, rvol) for k in ("1m", "5m", "15m")},
        high_of_day=price, low_of_day=98, prior_day_high=109, prior_day_low=95,
        prior_day_close=102, opening_range_high=110, opening_range_low=97)

