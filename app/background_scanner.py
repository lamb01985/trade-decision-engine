"""Hosted scanner lifecycle orchestration around the existing production engine."""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic

from app.config import Settings
from app.freshness import MarketSession, market_session
from app.notifications import NotificationService
from app.persistence.contracts import ScannerHeartbeat, ScannerStateStore
from app.service import AnalysisService
from app.universe import UniverseSelection

logger = logging.getLogger(__name__)


@dataclass
class ScannerRuntimeStatus:
    enabled: bool = False
    running: bool = False
    last_scan_started_at: datetime | None = None
    last_scan_completed_at: datetime | None = None
    last_successful_scan_at: datetime | None = None
    last_symbol_count: int = 0
    last_error_count: int = 0
    last_transition_count: int = 0
    last_data_status: str | None = None
    last_error_code: str | None = None


class HostedBackgroundScanner:
    scanner_scope = "hosted"

    def __init__(self, service: AnalysisService, state_store: ScannerStateStore,
                 notifications: NotificationService, config: Settings) -> None:
        self.service = service
        self.state_store = state_store
        self.notifications = notifications
        self.config = config
        self.owner_id = uuid.uuid4().hex
        self.runtime = ScannerRuntimeStatus(enabled=True)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._cycle_lock = asyncio.Lock()
        self._started_at = datetime.now(timezone.utc)
        self.selection = UniverseSelection(watchlist=config.hosted_scanner_watchlist,
            include_nasdaq_100=config.hosted_scanner_include_nasdaq_100,
            include_sp_500=config.hosted_scanner_include_sp_500,
            additional=config.hosted_scanner_additional_symbols)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="hosted-background-scanner")
            logger.info("hosted_scanner_started universe_size=%d notifications_enabled=%s",
                        len(self.selection.symbols()), self.config.notifications_enabled)

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None
        self.runtime.running = False
        logger.info("hosted_scanner_stopped")

    def _interval(self) -> int:
        session = market_session()
        if session is MarketSession.REGULAR:
            return self.config.scanner_refresh_seconds
        if session is MarketSession.PREMARKET:
            return self.config.scanner_premarket_interval_seconds
        return self.config.scanner_closed_interval_seconds

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.runtime.last_error_code = type(exc).__name__
                logger.warning("hosted_scanner_loop_error error_type=%s", type(exc).__name__)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval())
            except asyncio.TimeoutError:
                continue

    async def run_cycle(self) -> bool:
        if self._cycle_lock.locked():
            logger.warning("hosted_scanner_cycle_skipped reason=local_overlap")
            return False
        async with self._cycle_lock:
            started = datetime.now(timezone.utc)
            try:
                acquired = self.state_store.acquire_cycle(self.scanner_scope, self.owner_id, started,
                    timedelta(seconds=self.config.scanner_cycle_lease_seconds))
            except Exception as exc:
                self.runtime.last_error_code = f"DATABASE_{type(exc).__name__}"
                logger.warning("hosted_scanner_cycle_skipped reason=database error_type=%s",
                               type(exc).__name__)
                return False
            if not acquired:
                logger.info("hosted_scanner_cycle_skipped reason=distributed_overlap")
                return False

            symbols = self.selection.symbols()
            started_clock = monotonic()
            self.runtime.running = True
            self.runtime.last_scan_started_at = started
            error_code: str | None = None
            result = None
            logger.info("hosted_scanner_cycle_start symbols=%d", len(symbols))
            try:
                result = await self.service.market_scan(self.selection, force=True,
                    durable_transitions_only=True)
                await self.notifications.process(result.alerts, result.data_status, result.market_session)
                successful = not result.failed_symbols and result.data_status != "ERROR"
                error_count = len(result.failed_symbols) + (0 if successful else 1)
                transition_count = len(result.alerts)
                self.runtime.last_symbol_count = result.universe_size
                self.runtime.last_error_count = error_count
                self.runtime.last_transition_count = transition_count
                self.runtime.last_data_status = result.data_status
                if successful:
                    self.runtime.last_successful_scan_at = datetime.now(timezone.utc)
                self.runtime.last_error_code = None if successful else "PARTIAL_OR_STALE_DATA"
                error_code = self.runtime.last_error_code
            except asyncio.CancelledError:
                error_code = "CANCELLED"
                self.runtime.last_error_code = error_code
                raise
            except Exception as exc:
                successful = False
                error_count = 1
                transition_count = 0
                error_code = type(exc).__name__
                self.runtime.last_error_code = error_code
                logger.warning("hosted_scanner_cycle_error error_type=%s", error_code)
            finally:
                completed = datetime.now(timezone.utc)
                self.runtime.running = False
                self.runtime.last_scan_completed_at = completed
                try:
                    self.state_store.complete_cycle(self.scanner_scope, self.owner_id,
                        completed_at=completed, successful=bool(result is not None and successful),
                        symbol_count=self.runtime.last_symbol_count,
                        error_count=error_count if "error_count" in locals() else 1,
                        transition_count=transition_count if "transition_count" in locals() else 0,
                        data_status=result.data_status if result is not None else None,
                        error_code=error_code)
                except Exception as exc:
                    self.runtime.last_error_code = f"DATABASE_{type(exc).__name__}"
                    logger.warning("hosted_scanner_heartbeat_error error_type=%s", type(exc).__name__)
                logger.info("hosted_scanner_cycle_complete symbols=%d transitions=%d errors=%d status=%s elapsed_seconds=%.3f",
                    self.runtime.last_symbol_count,
                    transition_count if "transition_count" in locals() else 0,
                    error_count if "error_count" in locals() else 1,
                    result.data_status if result is not None else "ERROR", monotonic() - started_clock)
            return True

    def _durable(self) -> ScannerHeartbeat | None:
        try:
            return self.state_store.heartbeat(self.scanner_scope)
        except Exception:
            self.runtime.last_error_code = "DATABASE_UNAVAILABLE"
            return None

    def status(self) -> dict[str, object]:
        durable = self._durable()
        payload = asdict(self.runtime)
        if durable is not None:
            payload.update({key: value for key, value in asdict(durable).items()
                            if key not in {"scanner_scope", "owner_id", "lease_expires_at", "updated_at"}})
        payload["enabled"] = True
        payload["notifications_enabled"] = self.config.notifications_enabled
        payload["universe_size"] = len(self.selection.symbols())
        payload["stale"] = self.is_stale(durable)
        return payload

    def is_stale(self, durable: ScannerHeartbeat | None = None) -> bool:
        if market_session() not in {MarketSession.PREMARKET, MarketSession.REGULAR}:
            return False
        heartbeat = durable if durable is not None else self._durable()
        reference = heartbeat.last_scan_completed_at if heartbeat else self.runtime.last_scan_completed_at
        if reference is None:
            return datetime.now(timezone.utc) - self._started_at > timedelta(
                seconds=self.config.scanner_stale_after_seconds)
        return datetime.now(timezone.utc) - reference > timedelta(
            seconds=self.config.scanner_stale_after_seconds)

    def ready(self) -> bool:
        task_alive = self._task is not None and not self._task.done()
        return task_alive and not self.is_stale() and not bool(
            self.runtime.last_error_code and self.runtime.last_error_code.startswith("DATABASE_"))
