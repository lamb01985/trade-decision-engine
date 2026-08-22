import sqlite3
from datetime import datetime, timezone

from app.models import (ActionNow, Bar, DecisionQuality, SetupFamily, SetupProgress,
    StandardDecision, Trend)
from app.parameters import DEFAULT_PARAMETERS
from app.signal_log import SignalLogger
from tests.test_alerts import analysis


def decision(symbol="NVDA", trigger=110.0):
    now = datetime.now(timezone.utc)
    return StandardDecision(symbol=symbol, timestamp=now, market_price=109.5,
        direction="LONG", setup_family=SetupFamily.BREAKOUT_RETEST, setup_state=SetupProgress.TRIGGERED,
        quality=DecisionQuality.A, action_now=ActionNow.PREPARE_LONG, trigger=trigger, invalidation=109.0,
        target_1=112.0, target_2=114.0, nearest_support=109.0, nearest_resistance=110.0,
        vwap_state="ABOVE", structure_5m=Trend.BULLISH, structure_15m=Trend.BULLISH, atr=0.5,
        reasoning=["test"], missing_confirmation=[], do_not_trade_if=[], what_changes_my_mind=[],
        state_changed_at=now)


def bar(minute=0):
    return Bar(timestamp=datetime.now(timezone.utc), open=109, high=110, low=108, close=109.5, volume=1000)


def test_signal_logger_reuses_a_single_persistent_connection(tmp_path):
    # BUG 3 regression: _connect() must not open a fresh sqlite3 connection
    # on every call. It should hand back the same long-lived connection.
    log = SignalLogger(tmp_path / "signals.sqlite3")
    first = log._connect()
    second = log._connect()
    assert first is second
    assert isinstance(first, sqlite3.Connection)

    result = analysis("NVDA")
    result = result.model_copy(update={"decision": decision()})
    log.observe(result, [bar()], DEFAULT_PARAMETERS)
    log.observe(result, [bar()], DEFAULT_PARAMETERS)

    # Still the same connection after real read/write activity.
    assert log._connect() is first
    log.close()


def test_signal_logger_close_releases_the_connection(tmp_path):
    log = SignalLogger(tmp_path / "signals.sqlite3")
    log.close()
    try:
        log._connect().execute("SELECT 1")
        assert False, "expected use-after-close to raise"
    except sqlite3.ProgrammingError:
        pass
