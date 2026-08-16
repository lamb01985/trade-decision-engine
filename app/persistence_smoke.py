"""Two-process, production-safe RDS persistence probe with no external alerts."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

from app.models import ActionNow, DecisionQuality, SetupFamily, SetupProgress, StandardDecision, Trend
from app.parameters import DEFAULT_PARAMETERS
from app.persistence import ScannerState, create_stores
from app.signal_identity import signal_fingerprint


SYMBOL = "STAGE2E"


def stores():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    return create_stores(url, initialize=os.getenv("SMOKE_INITIALIZE_SCHEMA") == "1",
                         connect_timeout=10, pool_size=1, max_overflow=0)


def decision(at: datetime, state: SetupProgress, prior: SetupProgress | None = None) -> StandardDecision:
    return StandardDecision(symbol=SYMBOL, timestamp=at, market_price=100.0, direction="LONG",
        setup_family=SetupFamily.BREAKOUT_RETEST, setup_state=state, quality=DecisionQuality.A,
        action_now=ActionNow.WAIT if state is SetupProgress.WATCHING else ActionNow.LONG_TRIGGERED,
        trigger=100.0, invalidation=99.0, target_1=101.0, target_2=102.0,
        structural_risk_reward=2.0, nearest_support=99.0, nearest_resistance=101.0,
        vwap_state="ABOVE", structure_5m=Trend.BULLISH, structure_15m=Trend.BULLISH,
        atr=1.0, reasoning=["Stage 2E persistence probe"], missing_confirmation=[],
        do_not_trade_if=[], what_changes_my_mind=[], prior_state=prior, state_changed_at=at)


def fingerprint_for(item: StandardDecision) -> str:
    return signal_fingerprint(symbol=item.symbol, setup_family=item.setup_family.value,
        direction=item.direction, trigger=item.trigger, session=item.timestamp,
        engine_version=item.engine_version, setup_rules_version=item.setup_rules_version)


def write() -> dict[str, object]:
    persistence = stores()
    at = datetime.now(timezone.utc).replace(microsecond=0)
    initial = decision(at, SetupProgress.WATCHING)
    fingerprint = persistence.signal.record_decision(initial,
        parameter_snapshot=DEFAULT_PARAMETERS.as_dict(), evidence={"levels": []})
    later = at + timedelta(seconds=1)
    confirmed = decision(later, SetupProgress.CONFIRMED, SetupProgress.WATCHING)
    persistence.signal.record_decision(confirmed, parameter_snapshot=DEFAULT_PARAMETERS.as_dict(),
        evidence={"levels": []})
    persistence.scanner.advance(ScannerState("stage2e", "production-smoke", SYMBOL, "READY",
        fingerprint, later, later, confirmed.market_price, 100, later))
    event = f"{fingerprint}:READY"
    reserved = persistence.notification.reserve(event, "READY")
    persistence.notification.mark_sent(event)
    persistence.signal.close()
    return {"mode": "write", "fingerprint": fingerprint, "notification_reserved": reserved}


def read() -> dict[str, object]:
    persistence = stores()
    current = persistence.signal.get_latest_decision(SYMBOL)
    if current is None:
        raise AssertionError("probe signal was not found")
    fingerprint = fingerprint_for(current)
    scanner = persistence.scanner.get("stage2e", "production-smoke", SYMBOL)
    event = f"{fingerprint}:READY"
    duplicate = persistence.notification.reserve(event, "READY")
    notice = persistence.notification.notification(event)
    transitions = persistence.signal.transitions(fingerprint)
    persistence.signal.close()
    assert current.setup_state is SetupProgress.CONFIRMED
    assert scanner is not None and scanner.state == "READY"
    assert notice is not None and notice["status"] == "SENT"
    assert duplicate is False and len(transitions) == 2
    return {"mode": "read", "fingerprint": fingerprint, "state": current.setup_state.value,
        "scanner_state": scanner.state, "notification_status": notice["status"],
        "duplicate_accepted": duplicate, "transition_count": len(transitions)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("write", "read"))
    args = parser.parse_args()
    print(json.dumps(write() if args.mode == "write" else read(), sort_keys=True))
