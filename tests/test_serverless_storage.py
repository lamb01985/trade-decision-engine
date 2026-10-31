import asyncio
from dataclasses import replace
from pathlib import Path

from tests.auth_client import EngineTestClient as TestClient, engine_security

import app.main as main_module
from app.config import StorageMode
from app.notifications import NotificationService
from app.service import AnalysisService
from app.signal_log import NoOpSignalStore, SignalLogger
from tests.test_service_v2 import FakeProvider


def test_hosted_startup_does_not_write_project_filesystem(monkeypatch, tmp_path):
    hosted = replace(main_module.settings, storage_mode=StorageMode.HOSTED,
                     massive_api_key="configured", pushover_user_key=None,
                     pushover_app_token=None)
    monkeypatch.setattr(main_module, "settings", hosted)
    monkeypatch.setattr(main_module, "MassiveMarketDataProvider", lambda *_: FakeProvider())
    original_mkdir = Path.mkdir

    def reject_project_writes(path, *args, **kwargs):
        if Path(path).resolve().is_relative_to(Path.cwd().resolve()):
            raise OSError(30, "Read-only file system", str(path))
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", reject_project_writes)
    with TestClient(main_module.app) as client:
        assert client.get("/").status_code == 200
        readiness = client.get("/api/readiness").json()
        assert readiness["ready"] is True
        assert readiness["signal_store_available"] is False
        assert readiness["notification_store_available"] is False
        assert readiness["capabilities"]["interactive_analysis"] is True
        assert readiness["capabilities"]["persistent_signal_store"] is False


def test_interactive_analysis_works_with_noop_store():
    async def scenario():
        service = AnalysisService(FakeProvider(), signal_logger=NoOpSignalStore())
        result = await service.analyze_symbol("NVDA")
        assert result.instrument.symbol == "NVDA"
        assert service.signal_logger.persistent is False
    asyncio.run(scenario())


def test_local_sqlite_signal_and_notification_storage_remains_durable(tmp_path):
    signal_path = tmp_path / "signals" / "signals.sqlite3"
    notification_path = tmp_path / "notifications" / "state.sqlite3"
    logger = SignalLogger(signal_path)
    notifications = NotificationService(None, notification_path)
    assert signal_path.exists() and logger.persistent is True
    assert notification_path.exists() and notifications.persistent is True


def test_hosted_notification_manual_test_is_safe_without_dedup_store():
    async def scenario():
        service = NotificationService(None, None)
        assert service.persistent is False
        assert service.status()["automatic_alerts_available"] is False
        assert (await service.send_test())["status"] == "DISABLED"
    asyncio.run(scenario())


def test_capability_payload_contains_no_secrets(monkeypatch):
    hosted = replace(main_module.settings, storage_mode=StorageMode.HOSTED,
                     massive_api_key="secret-market-key", pushover_user_key="secret-user",
                     pushover_app_token="secret-token")
    monkeypatch.setattr(main_module, "settings", hosted)
    payload = str(main_module._capabilities()).lower()
    assert "secret" not in payload and "token" not in payload and "api_key" not in payload


def test_research_modules_remain_importable_locally():
    from app import backtesting, research
    assert callable(backtesting.run_backtest)
    assert callable(research.replay)
