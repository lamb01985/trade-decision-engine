from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.freshness import classify_data, is_early_close, market_data_state
from app.models import Bar, SymbolAnalysis

ET = ZoneInfo("America/New_York")


class ChartTimeframe(str, Enum):
    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    ONE_HOUR = "1h"
    ONE_DAY = "1d"


class ChartCandle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class ChartOverlay(BaseModel):
    id: str
    kind: str
    label: str
    value: float | None = None
    lower: float | None = None
    upper: float | None = None
    color: str
    default_visible: bool = False
    timestamp: datetime | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class ChartSnapshot(BaseModel):
    schema_version: str = "1.0"
    symbol: str
    timeframe: ChartTimeframe
    extended_hours: bool
    candles: list[ChartCandle]
    overlays: list[ChartOverlay]
    market_status: str
    market_session: str
    provider_timestamp: datetime
    generated_timestamp: datetime
    data_through_timestamp: datetime
    data_age_seconds: int
    stale: bool
    source: str = "MASSIVE_AGGREGATES"
    forming_bar_included: bool = False


_MINUTES = {ChartTimeframe.ONE_MINUTE: 1, ChartTimeframe.FIVE_MINUTES: 5,
            ChartTimeframe.FIFTEEN_MINUTES: 15, ChartTimeframe.ONE_HOUR: 60}


def _in_session(bar: Bar, extended: bool) -> bool:
    clock = bar.timestamp.astimezone(ET).time().replace(tzinfo=None)
    return (time(4) <= clock < time(20)) if extended else (time(9, 30) <= clock < time(16))


def _aggregate(group: list[Bar]) -> Bar:
    return Bar(timestamp=group[0].timestamp, open=group[0].open,
               high=max(item.high for item in group), low=min(item.low for item in group),
               close=group[-1].close, volume=sum(item.volume for item in group))


def completed_chart_bars(bars: list[Bar], timeframe: ChartTimeframe, extended: bool,
                         now: datetime | None = None) -> list[Bar]:
    """Filter sessions and aggregate only buckets whose deterministic end has passed."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    minutes = _MINUTES.get(timeframe)
    groups: dict[tuple, list[Bar]] = defaultdict(list)
    for bar in sorted(bars, key=lambda item: item.timestamp):
        if not _in_session(bar, extended) or bar.timestamp + timedelta(minutes=1) > current:
            continue
        local = bar.timestamp.astimezone(ET)
        if timeframe == ChartTimeframe.ONE_DAY:
            key = (local.date(),)
        else:
            session_start = 4 * 60 if extended else 9 * 60 + 30
            offset = local.hour * 60 + local.minute - session_start
            key = (local.date(), offset // minutes)
        groups[key].append(bar)
    completed: list[Bar] = []
    for key, group in groups.items():
        local_day = group[0].timestamp.astimezone(ET).date()
        if timeframe == ChartTimeframe.ONE_DAY:
            close_clock = time(20) if extended else (time(13) if is_early_close(local_day) else time(16))
            bucket_end = datetime.combine(local_day, close_clock, ET).astimezone(timezone.utc)
        else:
            bucket_end = group[0].timestamp + timedelta(minutes=minutes)
        if bucket_end <= current:
            completed.append(_aggregate(group))
    return sorted(completed, key=lambda item: item.timestamp)


def analysis_overlays(analysis: SymbolAnalysis | None, timestamp: datetime) -> list[ChartOverlay]:
    if analysis is None:
        return []
    result: list[ChartOverlay] = []
    frame = analysis.instrument.timeframes.get("5m")
    values = [
        ("current-price", "current-price", "Current price", analysis.current_price, "#d8e5eb", True),
        ("vwap", "vwap", "VWAP", frame.vwap if frame else None, "#f0b44d", False),
        ("ema-9", "ema", "EMA 9", frame.ema_9 if frame else None, "#65b5ff", False),
        ("ema-21", "ema", "EMA 21", frame.ema_21 if frame else None, "#b68cff", False),
        ("invalidation", "invalidation", "Invalidation", analysis.trade_plan.invalidation, "#ef6f78", True),
        ("target-1", "target", "Target 1", analysis.trade_plan.target_1, "#42d392", True),
        ("target-2", "target", "Target 2", analysis.trade_plan.target_2, "#2dbd7f", True),
    ]
    trigger = analysis.decision.trigger if analysis.decision else analysis.trade_plan.trigger
    values.insert(1, ("decision-zone", "decision-zone", "Decision / entry", trigger, "#ffd166", True))
    for oid, kind, label, value, color, visible in values:
        if value is not None:
            result.append(ChartOverlay(id=oid, kind=kind, label=label, value=value,
                                       color=color, default_visible=visible, timestamp=timestamp))
    for index, level in enumerate(analysis.levels):
        if level.confluence > 1 or level.importance.value.startswith("MAJOR_"):
            result.append(ChartOverlay(id=f"structure-{index}", kind="structure",
                label=" / ".join(level.types), value=level.price, color="#8095a3",
                timestamp=timestamp, metadata={"importance": level.importance.value}))
    if analysis.decision_bar_timestamp:
        result.append(ChartOverlay(id="setup-marker", kind="setup-marker",
            label=(analysis.decision.setup_family.value if analysis.decision else analysis.setup.setup_type),
            color="#ffd166", timestamp=analysis.decision_bar_timestamp))
    return result


def build_snapshot(symbol: str, timeframe: ChartTimeframe, extended: bool, bars: list[Bar],
                   analysis: SymbolAnalysis | None, now: datetime | None = None) -> ChartSnapshot:
    generated = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    completed = completed_chart_bars(bars, timeframe, extended, generated)
    if not completed:
        raise ValueError(f"No completed chart bars available for {symbol}")
    last = completed[-1]
    if timeframe == ChartTimeframe.ONE_DAY:
        last_day = last.timestamp.astimezone(ET).date()
        close_clock = time(20) if extended else (time(13) if is_early_close(last_day) else time(16))
        data_through = datetime.combine(last_day, close_clock, ET).astimezone(timezone.utc)
    else:
        data_through = last.timestamp + timedelta(minutes=_MINUTES[timeframe])
    status, session, age = classify_data(data_through, generated)
    overlays = analysis_overlays(analysis, data_through)
    if not any(item.id == "current-price" for item in overlays):
        overlays.insert(0, ChartOverlay(id="current-price", kind="current-price",
            label="Current / last completed price", value=last.close, color="#d8e5eb",
            default_visible=True, timestamp=data_through))
    return ChartSnapshot(symbol=symbol, timeframe=timeframe, extended_hours=extended,
        candles=[ChartCandle(timestamp=item.timestamp, open=item.open, high=item.high,
                            low=item.low, close=item.close, volume=item.volume) for item in completed],
        overlays=overlays,
        market_status=market_data_state(status, session).value, market_session=session.value,
        provider_timestamp=data_through, generated_timestamp=generated,
        data_through_timestamp=data_through, data_age_seconds=age or 0,
        stale=market_data_state(status, session).value != "LIVE_CURRENT")
