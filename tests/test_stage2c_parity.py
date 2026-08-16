import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone

import pytest
from sqlalchemy import event

from app.models import SetupProgress
from app.parameters import DEFAULT_PARAMETERS
from app.persistence import ScannerState, create_stores
from app.signal_identity import signal_fingerprint
from app.signal_log import NoOpSignalStore
from app.service import AnalysisService
from tests.test_service_v2 import FakeProvider

POSTGRES_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TEST_DATABASE_URL is not configured")


def unique_symbol(prefix: str) -> str:
    """Keep persistent integration-test keys isolated across repeated suite runs."""
    prefix = prefix.upper()[:4]
    return f"{prefix}{uuid.uuid4().hex[:10-len(prefix)]}".upper()


def representative_decision(symbol="PARITY"):
    analysis = asyncio.run(AnalysisService(FakeProvider(), signal_logger=NoOpSignalStore()).analyze_symbol(symbol))
    decision = analysis.decision
    return decision.model_copy(update={"trigger": (decision.trigger or decision.market_price) +
        (uuid.uuid4().int % 10000) / 1_000_000})


def normalized_time(value):
    if value is None: return None
    if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def workflow(stores, initial):
    first_fingerprint = stores.signal.record_decision(initial,
        parameter_snapshot=DEFAULT_PARAMETERS.as_dict(), evidence={"levels": []})
    later = initial.timestamp + timedelta(minutes=5)
    final = initial.model_copy(update={"setup_state": SetupProgress.CONFIRMED,
        "prior_state": initial.setup_state, "timestamp": later, "state_changed_at": later})
    assert stores.signal.record_decision(final, parameter_snapshot=DEFAULT_PARAMETERS.as_dict(),
        evidence={"levels": []}) == first_fingerprint
    hit_at = later + timedelta(minutes=5)
    stores.signal.update_outcome(first_fingerprint, mfe_price=4.25, mae_price=1.5,
        target_1_hit=True, target_1_hit_at=hit_at, invalidation_hit=False,
        last_evidence_at=hit_at)
    scanner_scope = f"stage2c-{first_fingerprint}"
    scanner = ScannerState(scanner_scope, "parity-policy", initial.symbol, "READY",
        '["LONG","representative"]', later, later, initial.market_price, 88, later)
    assert stores.scanner.advance(scanner)
    stores.notification.update_preferences({"allow_b_quality": True, "radar": False})
    event_key = f"{first_fingerprint}:READY"
    assert stores.notification.reserve(event_key, "READY")
    stores.notification.mark_sent(event_key)
    assert not stores.notification.reserve(event_key, "READY")
    signal = stores.signal.get_signal(first_fingerprint)
    outcome = stores.signal.outcome(first_fingerprint)
    restored_scanner = stores.scanner.get(scanner_scope, "parity-policy", initial.symbol)
    notification = stores.notification.notification(event_key)
    transitions = stores.signal.transitions(first_fingerprint)
    return {
        "fingerprint": first_fingerprint,
        "signal": {key: signal[key] for key in ("symbol", "setup_family", "direction",
            "current_state", "engine_version", "setup_rules_version", "parameter_version")},
        "latest_state": stores.signal.get_latest_decision(initial.symbol).setup_state.value,
        "transitions": [(row["from_state"], row["to_state"], normalized_time(row["transition_at"]))
                        for row in transitions],
        "outcome": {"mfe_price": outcome["mfe_price"], "mae_price": outcome["mae_price"],
            "target_1_hit": bool(outcome["target_1_hit"]),
            "invalidation_hit": bool(outcome["invalidation_hit"]),
            "target_1_hit_at": normalized_time(outcome["target_1_hit_at"])},
        "scanner": {"state": restored_scanner.state,
            "candidate_fingerprint": restored_scanner.candidate_fingerprint,
            "last_observed_at": normalized_time(restored_scanner.last_observed_at)},
        "preferences": stores.notification.preferences(),
        "notification": {"event_type": notification["event_type"], "status": notification["status"],
            "attempt_count": notification["attempt_count"]},
    }


def test_sqlite_and_postgres_have_identical_business_semantics(tmp_path):
    decision = representative_decision(unique_symbol("PAR"))
    sqlite = create_stores(f"sqlite:///{tmp_path / 'parity.sqlite3'}", initialize=True)
    postgres = create_stores(POSTGRES_URL)
    assert workflow(sqlite, decision) == workflow(postgres, decision)


def test_competing_lifecycle_updates_keep_newest_state():
    repository = create_stores(POSTGRES_URL).signal
    initial = representative_decision(unique_symbol("LIFE"))
    fingerprint = repository.record_decision(initial,
        parameter_snapshot=DEFAULT_PARAMETERS.as_dict(), evidence={"levels": []})
    older_at = initial.timestamp + timedelta(minutes=5)
    newer_at = initial.timestamp + timedelta(minutes=10)
    older = initial.model_copy(update={"setup_state": SetupProgress.CONFIRMED,
        "timestamp": older_at, "state_changed_at": older_at})
    newer = initial.model_copy(update={"setup_state": SetupProgress.INVALIDATED,
        "timestamp": newer_at, "state_changed_at": newer_at})
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda item: repository.record_decision(item,
            parameter_snapshot=DEFAULT_PARAMETERS.as_dict(), evidence={"levels": []}), (newer, older)))
    assert repository.get_latest_decision(initial.symbol).setup_state == SetupProgress.INVALIDATED
    times = [normalized_time(row["observed_at"]) for row in repository.transitions(fingerprint)]
    assert times == sorted(times)


def test_record_decision_rolls_back_all_writes_when_outcome_insert_fails():
    repository = create_stores(POSTGRES_URL).signal
    decision = representative_decision(unique_symbol("ROLL"))
    fingerprint = signal_fingerprint(symbol=decision.symbol,
        setup_family=decision.setup_family.value, direction=decision.direction,
        trigger=decision.trigger, session=decision.timestamp,
        engine_version=decision.engine_version, setup_rules_version=decision.setup_rules_version)

    def fail_outcome(_, __, statement, ___, ____, _____):
        if statement.lstrip().startswith("INSERT INTO signal_outcomes"):
            raise RuntimeError("intentional rollback probe")

    event.listen(repository.engine, "before_cursor_execute", fail_outcome)
    try:
        with pytest.raises(RuntimeError, match="intentional rollback probe"):
            repository.record_decision(decision,
                parameter_snapshot=DEFAULT_PARAMETERS.as_dict(), evidence={"levels": []})
    finally:
        event.remove(repository.engine, "before_cursor_execute", fail_outcome)
    assert repository.get_signal(fingerprint) is None
    assert repository.transitions(fingerprint) == []
