"""One-cycle local hosted-scanner probe. Uses FakeProvider and never sends notifications."""
import asyncio
import json
import os
from dataclasses import replace

from app.background_scanner import HostedBackgroundScanner
from app.config import StorageMode, settings
from app.notifications import NotificationService
from app.persistence import create_stores
from app.service import AnalysisService
from tests.test_service_v2 import FakeProvider


async def main() -> None:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        raise SystemExit("TEST_DATABASE_URL is required")
    stores = create_stores(url)
    config = replace(settings, storage_mode=StorageMode.HOSTED, hosted_scanner_enabled=True,
        notifications_enabled=False, hosted_scanner_watchlist=("NVDA", "AMD"),
        hosted_scanner_additional_symbols=(), hosted_scanner_include_nasdaq_100=False,
        hosted_scanner_include_sp_500=False)
    service = AnalysisService(FakeProvider(), signal_logger=stores.signal,
                              scanner_state_store=stores.scanner)
    notifications = NotificationService(None, None, store=stores.notification)
    scanner = HostedBackgroundScanner(service, stores.scanner, notifications, config)
    completed = await scanner.run_cycle()
    status = scanner.status()
    assert completed and status["enabled"] and not status["running"]
    assert status["notifications_enabled"] is False and status["last_scan_completed_at"] is not None
    print(json.dumps(status, default=str, sort_keys=True))
    stores.signal.close()


if __name__ == "__main__":
    asyncio.run(main())
