import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.parameters import DEFAULT_PARAMETERS
from app.persistence import create_stores
from app.service import AnalysisService
from tests.test_service_v2 import FakeProvider

POSTGRES_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TEST_DATABASE_URL is not configured")


def stores():
    return create_stores(POSTGRES_URL)


def test_postgres_signal_lifecycle_versions_and_restart():
    first = stores()
    symbol = f"N{uuid.uuid4().hex[:7]}".upper()
    analysis = asyncio.run(AnalysisService(FakeProvider(), signal_logger=first.signal).analyze_symbol(symbol))
    decision = analysis.decision
    fingerprint = first.signal.record_decision(decision,
        parameter_snapshot=DEFAULT_PARAMETERS.as_dict(), evidence={"levels": []})
    first.signal.close()
    second = stores()
    restored = second.signal.get_latest_decision(symbol)
    assert restored == decision
    row = second.signal.get_signal(fingerprint)
    assert (row["engine_version"], row["setup_rules_version"], row["parameter_version"]) == (
        decision.engine_version, decision.setup_rules_version, decision.parameter_version)
    assert len(second.signal.transitions(fingerprint)) == 1


def test_postgres_duplicate_signal_is_concurrency_safe():
    repository = stores().signal
    analysis = asyncio.run(AnalysisService(FakeProvider(), signal_logger=repository).analyze_symbol("META"))
    decision = analysis.decision.model_copy(update={"trigger": analysis.decision.trigger +
        (uuid.uuid4().int % 10000) / 1_000_000})
    with ThreadPoolExecutor(max_workers=4) as pool:
        fingerprints = list(pool.map(lambda _: repository.record_decision(decision,
            parameter_snapshot=DEFAULT_PARAMETERS.as_dict(), evidence={"levels": []}), range(4)))
    assert len(set(fingerprints)) == 1
    assert repository.get_signal(fingerprints[0]) is not None


def test_postgres_notification_constraint_is_concurrency_safe():
    repository = stores().notification
    event = f"postgres-concurrent-event-{uuid.uuid4()}"
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: repository.reserve(event, "READY"), range(6)))
    assert results.count(True) == 1
    repository.mark_sent(event)
    assert repository.notification(event)["status"] == "SENT"


def test_postgres_outcome_is_monotonic():
    repository = stores().signal
    analysis = asyncio.run(AnalysisService(FakeProvider(), signal_logger=repository).analyze_symbol("AMD"))
    fingerprint = repository.record_decision(analysis.decision,
        parameter_snapshot=DEFAULT_PARAMETERS.as_dict(), evidence={"levels": []})
    repository.update_outcome(fingerprint, mfe_price=4, mae_price=2, target_1_hit=True)
    repository.update_outcome(fingerprint, mfe_price=1, mae_price=1, target_1_hit=False)
    outcome = repository.outcome(fingerprint)
    assert outcome["mfe_price"] == 4 and outcome["mae_price"] == 2 and outcome["target_1_hit"]


def test_postgres_scanner_cycle_lease_and_heartbeat_survive_restart():
    scope = f"hosted-{uuid.uuid4()}"
    first = stores()
    now = datetime.now(timezone.utc)
    assert first.scanner.acquire_cycle(scope, "process-a", now, timedelta(minutes=5))
    assert first.scanner.acquire_cycle(scope, "process-b", now, timedelta(minutes=5)) is False
    first.scanner.complete_cycle(scope, "process-a", completed_at=now + timedelta(seconds=1),
        successful=True, symbol_count=13, error_count=0, transition_count=2, data_status="LIVE")
    first.signal.close()
    restored = stores().scanner.heartbeat(scope)
    assert restored is not None and restored.running is False
    assert restored.last_symbol_count == 13 and restored.last_transition_count == 2
    assert restored.last_successful_scan_at is not None


def test_postgres_eligible_notification_can_be_atomically_promoted():
    repository = stores().notification
    event = f"postgres-eligible-{uuid.uuid4()}"
    assert repository.record_eligible(event, "READY") is True
    assert repository.record_eligible(event, "READY") is False
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: repository.reserve(event, "READY"), range(4)))
    assert results.count(True) == 1
    assert repository.notification(event)["status"] == "CLAIMED"
