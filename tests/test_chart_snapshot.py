from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.chart_snapshot import (ChartSnapshot, ChartTimeframe, analysis_overlays, build_snapshot,
                                completed_chart_bars)
from app.models import Bar


def bar(hour: int, minute: int, price: float = 100, day: int = 14) -> Bar:
    # August is EDT; 13:30 UTC is the regular open.
    return Bar(timestamp=datetime(2026, 8, day, hour, minute, tzinfo=timezone.utc),
               open=price, high=price + 1, low=price - 1, close=price + .5, volume=10)


@pytest.mark.parametrize("timeframe,expected", [
    (ChartTimeframe.ONE_MINUTE, 10), (ChartTimeframe.FIVE_MINUTES, 2),
    (ChartTimeframe.FIFTEEN_MINUTES, 0), (ChartTimeframe.ONE_HOUR, 0),
])
def test_supported_intraday_timeframes_only_return_completed_buckets(timeframe, expected):
    bars = [bar(13, 30 + minute, 100 + minute) for minute in range(10)]
    now = datetime(2026, 8, 14, 13, 40, tzinfo=timezone.utc)
    result = completed_chart_bars(bars, timeframe, False, now)
    assert len(result) == expected
    assert all(left.timestamp < right.timestamp for left, right in zip(result, result[1:]))


def test_regular_session_filter_excludes_extended_hours_without_fabricating_bars():
    bars = [bar(12, 0), bar(13, 30), bar(19, 0), bar(21, 0)]
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    regular = completed_chart_bars(bars, ChartTimeframe.ONE_MINUTE, False, now)
    extended = completed_chart_bars(bars, ChartTimeframe.ONE_MINUTE, True, now)
    assert [item.timestamp.hour for item in regular] == [13, 19]
    assert [item.timestamp.hour for item in extended] == [12, 13, 19, 21]
    assert sum(item.volume for item in regular) == 20


def test_currently_forming_bar_is_not_returned():
    now = datetime(2026, 8, 14, 13, 35, 30, tzinfo=timezone.utc)
    bars = [bar(13, minute) for minute in range(30, 36)]
    result = completed_chart_bars(bars, ChartTimeframe.FIVE_MINUTES, False, now)
    assert [item.timestamp.minute for item in result] == [30]


def test_market_closed_snapshot_preserves_data_through_and_stale_metadata():
    bars = [bar(19, minute) for minute in range(50, 60)]
    now = datetime(2026, 8, 16, 12, tzinfo=timezone.utc)  # Sunday
    result = build_snapshot("QQQ", ChartTimeframe.FIVE_MINUTES, False, bars, None, now)
    assert result.market_status == "MARKET_CLOSED_LAST_SESSION"
    assert result.market_session == "CLOSED"
    assert result.data_through_timestamp == datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)
    assert result.stale is True
    assert result.forming_bar_included is False
    assert [item.id for item in result.overlays] == ["current-price"]
    assert result.overlays[0].label == "Current / last completed price"


def test_open_market_completed_bar_is_fresh():
    bars = [bar(13, minute) for minute in range(30, 35)]
    now = datetime(2026, 8, 14, 13, 35, tzinfo=timezone.utc)
    result = build_snapshot("QQQ", ChartTimeframe.FIVE_MINUTES, False, bars, None, now)
    assert result.market_status == "LIVE_CURRENT"
    assert result.market_session == "REGULAR"
    assert result.data_age_seconds == 0
    assert result.stale is False


def test_daily_bar_requires_session_completion():
    bars = [bar(13, 30 + minute) for minute in range(5)]
    before_close = completed_chart_bars(bars, ChartTimeframe.ONE_DAY, False,
        datetime(2026, 8, 14, 19, tzinfo=timezone.utc))
    after_close = completed_chart_bars(bars, ChartTimeframe.ONE_DAY, False,
        datetime(2026, 8, 14, 21, tzinfo=timezone.utc))
    assert before_close == []
    assert len(after_close) == 1


def test_ohlcv_schema_rejects_missing_or_malformed_candles():
    with pytest.raises(ValidationError):
        ChartSnapshot.model_validate({"symbol": "QQQ", "timeframe": "5m"})


def test_malformed_timeframe_is_rejected():
    with pytest.raises(ValueError):
        ChartTimeframe("2m")


def test_no_completed_data_is_explicitly_unavailable():
    with pytest.raises(ValueError, match="No completed chart bars"):
        build_snapshot("NONE", ChartTimeframe.FIVE_MINUTES, False, [], None)


def test_cached_analysis_adds_authoritative_default_and_optional_overlays():
    plan = SimpleNamespace(trigger=101, invalidation=98, target_1=104, target_2=107)
    frame = SimpleNamespace(vwap=100, ema_9=100.5, ema_21=99.5)
    analysis = SimpleNamespace(current_price=101.5, trade_plan=plan, decision=None,
        instrument=SimpleNamespace(timeframes={"5m": frame}), levels=[],
        decision_bar_timestamp=None, setup=SimpleNamespace(setup_type="NONE"))
    overlays = analysis_overlays(analysis, datetime(2026, 8, 14, 20, tzinfo=timezone.utc))
    by_id = {item.id: item for item in overlays}
    assert {"current-price", "decision-zone", "invalidation", "target-1", "target-2",
            "vwap", "ema-9", "ema-21"} <= set(by_id)
    assert by_id["current-price"].default_visible is True
    assert by_id["vwap"].default_visible is False
