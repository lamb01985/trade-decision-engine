"""Two-process Stage 2C probe. Run `write`, terminate, then run `read`."""
import argparse
import asyncio
import json
import os
from datetime import timedelta

from app.models import SetupProgress
from app.parameters import DEFAULT_PARAMETERS
from app.persistence import ScannerState, create_stores
from app.signal_identity import signal_fingerprint
from app.signal_log import NoOpSignalStore
from app.service import AnalysisService
from tests.test_service_v2 import FakeProvider


def stores():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url: raise SystemExit("TEST_DATABASE_URL is required")
    return create_stores(url)


def write() -> dict:
    persistence = stores()
    analysis = asyncio.run(AnalysisService(FakeProvider(), signal_logger=NoOpSignalStore()).analyze_symbol("RESTART"))
    initial = analysis.decision
    fingerprint = persistence.signal.record_decision(initial,
        parameter_snapshot=DEFAULT_PARAMETERS.as_dict(), evidence={"levels": []})
    later = initial.timestamp + timedelta(minutes=5)
    final = initial.model_copy(update={"setup_state": SetupProgress.CONFIRMED,
        "prior_state": initial.setup_state, "timestamp": later, "state_changed_at": later})
    persistence.signal.record_decision(final, parameter_snapshot=DEFAULT_PARAMETERS.as_dict(),
        evidence={"levels": []})
    persistence.scanner.advance(ScannerState("stage2c-restart", "policy", "RESTART", "READY",
        '["restart"]', later, later, final.market_price, 90, later))
    reserved = persistence.notification.reserve(f"{fingerprint}:READY", "READY")
    persistence.notification.mark_sent(f"{fingerprint}:READY")
    persistence.signal.close()
    return {"process": "A", "fingerprint": fingerprint, "reserved": reserved}


def read() -> dict:
    persistence = stores()
    decision = persistence.signal.get_latest_decision("RESTART")
    fingerprint = signal_fingerprint(symbol=decision.symbol,
        setup_family=decision.setup_family.value, direction=decision.direction,
        trigger=decision.trigger, session=decision.timestamp,
        engine_version=decision.engine_version, setup_rules_version=decision.setup_rules_version)
    scanner = persistence.scanner.get("stage2c-restart", "policy", "RESTART")
    duplicate = persistence.notification.reserve(f"{fingerprint}:READY", "READY")
    notification = persistence.notification.notification(f"{fingerprint}:READY")
    transitions = persistence.signal.transitions(fingerprint)
    persistence.signal.close()
    assert decision.setup_state == SetupProgress.CONFIRMED
    assert scanner is not None and scanner.state == "READY"
    assert notification is not None and notification["status"] == "SENT"
    assert duplicate is False and len(transitions) >= 2
    return {"process": "B", "fingerprint": fingerprint, "state": decision.setup_state.value,
        "scanner_state": scanner.state, "notification_status": notification["status"],
        "duplicate_accepted": duplicate, "transition_count": len(transitions)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("write", "read"))
    args = parser.parse_args()
    print(json.dumps(write() if args.mode == "write" else read(), sort_keys=True))
