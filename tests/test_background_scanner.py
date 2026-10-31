import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from tests.auth_client import EngineTestClient as TestClient, engine_security

import app.main as main_module
from app.background_scanner import HostedBackgroundScanner
from app.config import DatabaseBackend, StorageMode, settings
from app.engines.alerts import AlertEngine, ScannerPolicy
from app.freshness import DataStatus, MarketSession
from app.models import MarketScanResult
from app.notifications import NotificationService
from app.persistence import create_stores
from app.universe import UniverseSelection
from app.service import AnalysisService
from tests.test_alerts import analysis
from tests.test_service_v2 import FakeProvider


def scan_result(*, failed=(), alerts=(), status="LIVE"):
    now = datetime.now(timezone.utc)
    return MarketScanResult(universe_size=2, analyzed=2-len(failed), filtered_illiquid=0,
        candidates=[], alerts=list(alerts), provider_timestamp=now, analysis_timestamp=now,
        data_age_seconds=1, data_status=status, data_state="LIVE_CURRENT",
        market_session="REGULAR", failed_symbols=list(failed), next_refresh_seconds=300)


class FakeScanService:
    def __init__(self, result=None):
        self.result = result or scan_result()
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def market_scan(self, selection: UniverseSelection, force=False, durable_transitions_only=False):
        self.calls += 1
        self.entered.set()
        if self.block:
            await self.release.wait()
        return self.result


def scanner_config(**changes):
    return replace(settings, storage_mode=StorageMode.HOSTED, hosted_scanner_enabled=True,
        notifications_enabled=False, hosted_scanner_watchlist=("NVDA", "AMD"),
        scanner_refresh_seconds=300, scanner_premarket_interval_seconds=600,
        scanner_closed_interval_seconds=1800, scanner_stale_after_seconds=900,
        scanner_cycle_lease_seconds=900, **changes)


def components(tmp_path, service=None):
    stores = create_stores(f"sqlite:///{tmp_path / 'scanner.sqlite3'}", initialize=True)
    notifications = NotificationService(None, None, store=stores.notification)
    scanner = HostedBackgroundScanner(service or FakeScanService(), stores.scanner,
        notifications, scanner_config())
    return stores, notifications, scanner


def test_cycle_heartbeat_survives_process_replacement(tmp_path):
    async def scenario():
        stores, _, scanner = components(tmp_path)
        assert await scanner.run_cycle() is True
        heartbeat = stores.scanner.heartbeat("hosted")
        assert heartbeat is not None and heartbeat.last_successful_scan_at is not None
        assert heartbeat.last_symbol_count == 2 and heartbeat.last_error_count == 0
        stores.signal.close()
        restarted = create_stores(f"sqlite:///{tmp_path / 'scanner.sqlite3'}")
        restored = restarted.scanner.heartbeat("hosted")
        assert restored is not None and restored.last_successful_scan_at == heartbeat.last_successful_scan_at
    asyncio.run(scenario())


def test_local_overlap_is_skipped(tmp_path):
    async def scenario():
        service = FakeScanService(); service.block = True
        _, _, scanner = components(tmp_path, service)
        first = asyncio.create_task(scanner.run_cycle())
        await service.entered.wait()
        assert await scanner.run_cycle() is False
        service.release.set()
        assert await first is True and service.calls == 1
    asyncio.run(scenario())


def test_distributed_lease_prevents_second_process(tmp_path):
    stores, _, first = components(tmp_path)
    second = HostedBackgroundScanner(FakeScanService(), stores.scanner,
        NotificationService(None, None, store=stores.notification), scanner_config())
    now = datetime.now(timezone.utc)
    assert stores.scanner.acquire_cycle("hosted", first.owner_id, now, timedelta(minutes=5))
    assert stores.scanner.acquire_cycle("hosted", second.owner_id, now, timedelta(minutes=5)) is False


def test_start_and_clean_shutdown(tmp_path):
    async def scenario():
        _, _, scanner = components(tmp_path)
        scanner.start()
        await asyncio.sleep(.05)
        await scanner.stop()
        assert scanner._task is None and scanner.runtime.running is False
    asyncio.run(scenario())


def test_partial_symbol_failure_is_recorded_without_crashing_loop(tmp_path):
    async def scenario():
        stores, _, scanner = components(tmp_path, FakeScanService(scan_result(failed=("BAD",), status="ERROR")))
        assert await scanner.run_cycle() is True
        heartbeat = stores.scanner.heartbeat("hosted")
        assert heartbeat is not None and heartbeat.last_error_count == 2
        assert heartbeat.last_successful_scan_at is None
    asyncio.run(scenario())


def test_database_failure_is_clear_and_secret_safe(tmp_path, caplog):
    class FailedStore:
        def acquire_cycle(self, *args, **kwargs):
            raise RuntimeError("postgresql://user:sentinel-password@db")
        def heartbeat(self, *args):
            raise RuntimeError("sentinel-password")

    async def scenario():
        scanner = HostedBackgroundScanner(FakeScanService(), FailedStore(),
            NotificationService(None, None), scanner_config())
        assert await scanner.run_cycle() is False
        assert scanner.runtime.last_error_code == "DATABASE_RuntimeError"
        assert "sentinel-password" not in caplog.text
    asyncio.run(scenario())


def test_repeated_transition_is_durable_and_emitted_once(tmp_path):
    stores = create_stores(f"sqlite:///{tmp_path / 'transitions.sqlite3'}", initialize=True)
    first = AlertEngine(ScannerPolicy(), state_store=stores.scanner, scanner_scope="hosted")
    assert len(first.evaluate([analysis()])[1]) == 1
    restarted = AlertEngine(ScannerPolicy(), state_store=stores.scanner, scanner_scope="hosted")
    assert restarted.evaluate([analysis()])[1] == []


def test_notifications_disabled_records_eligibility_without_send(tmp_path):
    async def scenario():
        stores = create_stores(f"sqlite:///{tmp_path / 'eligible.sqlite3'}", initialize=True)
        alert = AlertEngine().evaluate([analysis()])[1][0]
        notifications = NotificationService(None, None, store=stores.notification)
        assert await notifications.process([alert], "LIVE", "REGULAR") == 0
        key = f"{notifications._fingerprint(alert)}:RADAR"
        assert stores.notification.notification(key)["status"] == "ELIGIBLE"
    asyncio.run(scenario())


def test_stale_background_observation_does_not_persist_transition(monkeypatch):
    class SpyStore:
        persistent = True
        def __init__(self): self.observations = 0
        def get_latest_decision(self, symbol): return None
        def observe(self, *args): self.observations += 1

    async def scenario():
        store = SpyStore()
        service = AnalysisService(FakeProvider(), signal_logger=store)
        monkeypatch.setattr("app.service.classify_data",
            lambda *args: (DataStatus.STALE, MarketSession.REGULAR, 9999))
        result = await service.analyze_symbol("NVDA", force=True, persist_actionable_only=True)
        assert result.data_status == "STALE" and store.observations == 0
        assert "NVDA" not in service._decisions
    asyncio.run(scenario())


def test_hosted_lifespan_autostarts_and_stops_scanner(monkeypatch, tmp_path):
    events = []

    class FakeBackground:
        def __init__(self, *args): pass
        def start(self): events.append("start")
        async def stop(self): events.append("stop")
        def ready(self): return True
        def status(self): return {"enabled": True, "running": False, "stale": False}

    configured = replace(main_module.settings, storage_mode=StorageMode.HOSTED,
        database_backend=DatabaseBackend.SQLITE,
        database_url=f"sqlite:///{tmp_path / 'app.sqlite3'}", massive_api_key="configured",
        hosted_scanner_enabled=True, notifications_enabled=False)
    monkeypatch.setattr(main_module, "settings", configured)
    monkeypatch.setattr(main_module, "MassiveMarketDataProvider", lambda *_: FakeProvider())
    monkeypatch.setattr(main_module, "HostedBackgroundScanner", FakeBackground)
    with TestClient(main_module.app) as client:
        assert events == ["start"]
        assert client.get("/api/scanner/status").json()["enabled"] is True
    assert events == ["start", "stop"]
