"""Live-only exact-unit validation. Legacy freshness remains untouched."""
import math
from datetime import datetime, timedelta, timezone
from app.market_data.instruments import POLICY_VERSION


def number(value, positive=False):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def timestamp(raw, unit):
    if isinstance(raw, bool) or not isinstance(raw, (int, str)) or not str(raw).isdigit():
        raise ValueError('INVALID_TIMESTAMP')
    divisor = {'NS': 1_000_000_000, 'MS': 1000}.get(unit)
    if not divisor:
        raise ValueError('INVALID_TIMESTAMP_UNIT')
    try:
        value=int(raw)
        return datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(seconds=value//divisor,microseconds=(value%divisor)//(divisor//1_000_000) if divisor>=1_000_000 else (value%divisor)*(1_000_000//divisor))
    except (OverflowError, OSError, ValueError):
        raise ValueError('INVALID_TIMESTAMP') from None


def freshness(time, now, sessions, feed='UNKNOWN', delay=None, threshold=30, session_time=None):
    if time is None:
        return 'UNKNOWN', None, ['MISSING_SOURCE_TIME']
    age = (now-time).total_seconds()
    if age < -5:
        return 'INVALID_CLOCK', age, ['INVALID_CLOCK']
    if age < 0:
        return 'UNKNOWN', age, ['CLOCK_SKEW']
    if feed == 'DELAYED':
        return ('STALE' if delay is not None and age > delay+60 else 'DELAYED'), age, ['DELAYED_FEED']
    if feed != 'REAL_TIME':
        return 'UNKNOWN', age, ['UNVERIFIED_FEED']
    if sessions.window(now.date()) is None:
        return 'UNKNOWN', age, ['SCHEDULE_UNKNOWN']
    source_session = sessions.at(session_time or time)
    if sessions.at(now):
        if not source_session:
            return 'UNKNOWN', age, ['EXTENDED_CONTEXT_ONLY']
        return ('CURRENT' if source_session == sessions.at(now) and age <= threshold else 'STALE'), age, []
    if source_session and source_session == sessions.latest_closed(now):
        return 'LAST_CLOSED_SESSION', age, []
    return ('UNKNOWN' if not source_session else 'STALE'), age, ['EXTENDED_CONTEXT_ONLY'] if not source_session else []


def quality(freshness='UNKNOWN', coverage='UNKNOWN', age=None, missing=None, invalid=None, reasons=None):
    return dict(freshness=freshness, coverage=coverage, policy_version=POLICY_VERSION, source_age_seconds=age,
                missing_fields=missing or [], invalid_fields=invalid or [], reasons=reasons or [])
