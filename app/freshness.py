from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
LIVE_MAX_AGE_SECONDS = 90
DELAYED_MAX_AGE_SECONDS = 300


class MarketSession(str, Enum):
    PREMARKET = "PREMARKET"
    REGULAR = "REGULAR"
    AFTER_HOURS = "AFTER_HOURS"
    CLOSED = "CLOSED"


class DataStatus(str, Enum):
    LIVE = "LIVE"
    DELAYED = "DELAYED"
    STALE = "STALE"
    MARKET_CLOSED = "MARKET_CLOSED"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


class MarketDataState(str, Enum):
    """Product-level state; deliberately separate from provider/session detail."""

    LIVE_CURRENT = "LIVE_CURRENT"
    LIVE_STALE = "LIVE_STALE"
    MARKET_CLOSED_LAST_SESSION = "MARKET_CLOSED_LAST_SESSION"
    DATA_ERROR = "DATA_ERROR"


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    day = date(year, month, 1)
    return day + timedelta(days=(weekday - day.weekday()) % 7 + 7 * (nth - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    first_next = date(year + (month == 12), month % 12 + 1, 1)
    day = first_next - timedelta(days=1)
    return day - timedelta(days=(day.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    # Anonymous Gregorian algorithm; Good Friday is an NYSE holiday.
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f, g = (b + 8) // 25, (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return date(year, month, (h + l - 7 * m + 114) % 31 + 1)


def market_holidays(year: int) -> set[date]:
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }
    # A following year's observed New Year can fall in this calendar year.
    holidays.add(_observed(date(year + 1, 1, 1)))
    return holidays


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in market_holidays(day.year)


def is_early_close(day: date) -> bool:
    if not is_trading_day(day):
        return False
    thanksgiving = _nth_weekday(day.year, 11, 3, 4)
    if day == thanksgiving + timedelta(days=1):
        return True
    if day.month == 7 and day.day == 3 and day.weekday() < 5:
        return True
    if day.month == 12 and day.day == 24 and day.weekday() < 5:
        return True
    return False


def market_session(now: datetime | None = None) -> MarketSession:
    current = (now or datetime.now(timezone.utc)).astimezone(ET)
    if not is_trading_day(current.date()):
        return MarketSession.CLOSED
    clock = current.time().replace(tzinfo=None)
    close = time(13, 0) if is_early_close(current.date()) else time(16, 0)
    if time(4, 0) <= clock < time(9, 30):
        return MarketSession.PREMARKET
    if time(9, 30) <= clock < close:
        return MarketSession.REGULAR
    if close <= clock < time(20, 0):
        return MarketSession.AFTER_HOURS
    return MarketSession.CLOSED


def data_age_seconds(provider_timestamp: datetime | None, now: datetime | None = None) -> int | None:
    if provider_timestamp is None:
        return None
    current = now or datetime.now(timezone.utc)
    timestamp = provider_timestamp if provider_timestamp.tzinfo else provider_timestamp.replace(tzinfo=timezone.utc)
    return max(0, int((current.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()))


def classify_data(provider_timestamp: datetime | None, now: datetime | None = None) -> tuple[DataStatus, MarketSession, int | None]:
    current = now or datetime.now(timezone.utc)
    session = market_session(current)
    age = data_age_seconds(provider_timestamp, current)
    if provider_timestamp is None:
        return DataStatus.UNAVAILABLE, session, None
    if session == MarketSession.CLOSED:
        return DataStatus.MARKET_CLOSED, session, age
    if age is not None and age <= LIVE_MAX_AGE_SECONDS:
        return DataStatus.LIVE, session, age
    if age is not None and age <= DELAYED_MAX_AGE_SECONDS:
        return DataStatus.DELAYED, session, age
    return DataStatus.STALE, session, age


def is_actionable_data(status: str | DataStatus, session: str | MarketSession) -> bool:
    return str(getattr(status, "value", status)) == DataStatus.LIVE.value and str(
        getattr(session, "value", session)
    ) == MarketSession.REGULAR.value


def market_data_state(status: str | DataStatus, session: str | MarketSession) -> MarketDataState:
    status_value = str(getattr(status, "value", status))
    session_value = str(getattr(session, "value", session))
    if status_value == DataStatus.MARKET_CLOSED.value and session_value == MarketSession.CLOSED.value:
        return MarketDataState.MARKET_CLOSED_LAST_SESSION
    if status_value == DataStatus.LIVE.value:
        return MarketDataState.LIVE_CURRENT
    if status_value in {DataStatus.DELAYED.value, DataStatus.STALE.value}:
        return MarketDataState.LIVE_STALE
    return MarketDataState.DATA_ERROR
