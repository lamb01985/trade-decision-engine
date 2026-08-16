import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from app.parameters import DEFAULT_PARAMETERS
from app.persistence import ScannerState, create_stores
from app.notifications import NotificationService
from app.service import AnalysisService
from tests.test_notifications import FakeProvider as FakeNotificationProvider, alert
from tests.test_service_v2 import FakeProvider


def sqlite_stores(tmp_path):
    return create_stores(f"sqlite:///{tmp_path / 'stage2b.sqlite3'}", initialize=True)


def decision_and_fingerprint(tmp_path):
    stores = sqlite_stores(tmp_path)
    async def run():
        service = AnalysisService(FakeProvider(), signal_logger=stores.signal)
        return await service.analyze_symbol("NVDA")
    analysis = asyncio.run(run())
    decision = analysis.decision
    assert decision is not None
    row = stores.signal.get_latest_decision("NVDA")
    assert row is not None
    fingerprint = stores.signal.record_decision(decision,
        parameter_snapshot=DEFAULT_PARAMETERS.as_dict(), evidence={"levels": []})
    return stores, decision, fingerprint


def test_sqlite_signal_is_idempotent_and_survives_restart(tmp_path):
    stores, decision, fingerprint = decision_and_fingerprint(tmp_path)
    assert stores.signal.get_signal(fingerprint)["engine_version"] == decision.engine_version
    stores.signal.close()
    restarted = create_stores(f"sqlite:///{tmp_path / 'stage2b.sqlite3'}")
    assert restarted.signal.get_latest_decision("NVDA") == decision
    assert len(restarted.signal.transitions(fingerprint)) == 1


def test_outcomes_are_monotonic(tmp_path):
    stores, _, fingerprint = decision_and_fingerprint(tmp_path)
    first = datetime.now(timezone.utc)
    stores.signal.update_outcome(fingerprint, mfe_price=3, mae_price=2,
        target_1_hit=True, target_1_hit_at=first)
    stores.signal.update_outcome(fingerprint, mfe_price=1, mae_price=1,
        target_1_hit=False, target_1_hit_at=first + timedelta(minutes=1))
    outcome = stores.signal.outcome(fingerprint)
    assert outcome["mfe_price"] == 3 and outcome["mae_price"] == 2
    assert outcome["target_1_hit"] is True
    assert outcome["target_1_hit_at"].replace(tzinfo=timezone.utc) == first.replace(tzinfo=timezone.utc)


def test_notification_unique_constraint_and_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'stage2b.sqlite3'}"
    stores = create_stores(url, initialize=True)
    assert stores.notification.reserve("same-event", "READY") is True
    assert stores.notification.reserve("same-event", "READY") is False
    stores.notification.mark_sent("same-event")
    stores.signal.close()
    restarted = create_stores(url)
    assert restarted.notification.reserve("same-event", "READY") is False
    assert restarted.notification.notification("same-event")["status"] == "SENT"


def test_notification_service_process_replacement_does_not_resend(tmp_path):
    url = f"sqlite:///{tmp_path / 'stage2b.sqlite3'}"
    process_a = create_stores(url, initialize=True)
    first_provider = FakeNotificationProvider()
    first = NotificationService(first_provider, None, store=process_a.notification)
    assert asyncio.run(first.process([alert()], "LIVE", "REGULAR")) == 1
    process_a.signal.close()
    process_b = create_stores(url)
    second_provider = FakeNotificationProvider()
    second = NotificationService(second_provider, None, store=process_b.notification)
    assert asyncio.run(second.process([alert()], "LIVE", "REGULAR")) == 0
    assert second_provider.messages == []


def test_disabled_provider_does_not_consume_notification_event(tmp_path):
    stores = sqlite_stores(tmp_path)
    disabled = NotificationService(None, None, store=stores.notification)
    assert asyncio.run(disabled.process([alert()], "LIVE", "REGULAR")) == 0
    enabled = NotificationService(FakeNotificationProvider(), None, store=stores.notification)
    assert asyncio.run(enabled.process([alert()], "LIVE", "REGULAR")) == 1


def test_concurrent_notification_reservation_has_one_winner(tmp_path):
    stores = sqlite_stores(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: stores.notification.reserve("race", "RADAR"), range(4)))
    assert results.count(True) == 1


def test_scanner_state_rejects_stale_writes(tmp_path):
    stores = sqlite_stores(tmp_path); now = datetime.now(timezone.utc)
    current = ScannerState("live", "policy", "NVDA", "RADAR", "one", now, now, 100, 80, now)
    stale = ScannerState("live", "policy", "NVDA", "READY", "two", now, now, 101, 90,
                         now - timedelta(seconds=1))
    assert stores.scanner.advance(current) is True
    assert stores.scanner.advance(stale) is False
    assert stores.scanner.get("live", "policy", "NVDA").state == "RADAR"


def test_failed_validation_does_not_mutate_outcome(tmp_path):
    stores, _, fingerprint = decision_and_fingerprint(tmp_path)
    before = dict(stores.signal.outcome(fingerprint))
    try:
        stores.signal.update_outcome(fingerprint, unsupported=1)
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported outcome field should fail")
    assert dict(stores.signal.outcome(fingerprint)) == before
