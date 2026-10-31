"""Versioned XNYS RTH schedules; missing calendar fails closed."""
from datetime import datetime, timedelta, timezone
from importlib.metadata import version

CALENDAR_VERSION = '4.13.2'


class Sessions:
    def __init__(self, calendar=None):
        self.calendar = calendar
        if calendar is None:
            try:
                import exchange_calendars as xc
                if version('exchange_calendars') == CALENDAR_VERSION:
                    self.calendar = xc.get_calendar('XNYS')
            except (ImportError, ValueError):
                pass

    def window(self, day):
        if self.calendar is None:
            return None
        try:
            import pandas as pd
            stamp = pd.Timestamp(day)
            if stamp < self.calendar.first_session or stamp > self.calendar.last_session:
                return None
            if not self.calendar.is_session(stamp):
                return ()
            return (self.calendar.session_open(stamp).to_pydatetime(), self.calendar.session_close(stamp).to_pydatetime())
        except (ValueError, TypeError, OverflowError):
            return None

    def at(self, time):
        window = self.window(time.date())
        if window and window[0] <= time < window[1]:
            return time.date().isoformat()
        return None

    def latest_closed(self, now):
        for offset in range(15):
            day = now.date() - timedelta(days=offset)
            window = self.window(day)
            if window is None:
                return None
            if window and window[1] <= now:
                return day.isoformat()
        return None
