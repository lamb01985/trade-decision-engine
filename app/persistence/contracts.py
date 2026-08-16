from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Protocol

from app.models import Bar, StandardDecision, SymbolAnalysis
from app.parameters import EngineParameters


class SignalStore(Protocol):
    persistent: bool
    def observe(self, analysis: SymbolAnalysis, bars: list[Bar], parameters: EngineParameters) -> None: ...
    def record_decision(self, decision: StandardDecision, *, parameter_snapshot: Mapping[str, Any],
                        evidence: Mapping[str, Any] | None = None) -> str: ...
    def get_latest_decision(self, symbol: str) -> StandardDecision | None: ...
    def get_signal(self, fingerprint: str) -> Mapping[str, Any] | None: ...
    def update_outcome(self, fingerprint: str, **values: Any) -> None: ...


@dataclass(frozen=True)
class ScannerState:
    scanner_scope: str
    policy_fingerprint: str
    symbol: str
    state: str
    candidate_fingerprint: str
    last_transition_at: datetime
    last_alert_at: datetime | None
    last_price: float
    last_score: int
    last_observed_at: datetime
    signal_id: str | None = None


@dataclass(frozen=True)
class ScannerHeartbeat:
    scanner_scope: str
    enabled: bool
    running: bool
    owner_id: str | None
    lease_expires_at: datetime | None
    last_scan_started_at: datetime | None
    last_scan_completed_at: datetime | None
    last_successful_scan_at: datetime | None
    last_symbol_count: int
    last_error_count: int
    last_transition_count: int
    last_data_status: str | None
    last_error_code: str | None
    updated_at: datetime


class ScannerStateStore(Protocol):
    persistent: bool
    def get(self, scanner_scope: str, policy_fingerprint: str, symbol: str) -> ScannerState | None: ...
    def advance(self, state: ScannerState) -> bool: ...
    def clear(self, scanner_scope: str, policy_fingerprint: str, symbol: str) -> None: ...
    def acquire_cycle(self, scanner_scope: str, owner_id: str, started_at: datetime,
                      lease: timedelta) -> bool: ...
    def complete_cycle(self, scanner_scope: str, owner_id: str, *, completed_at: datetime,
                       successful: bool, symbol_count: int, error_count: int,
                       transition_count: int, data_status: str | None,
                       error_code: str | None = None) -> None: ...
    def heartbeat(self, scanner_scope: str) -> ScannerHeartbeat | None: ...


class NotificationStore(Protocol):
    persistent: bool
    def preferences(self, destination_scope: str = "default") -> dict[str, bool]: ...
    def update_preferences(self, values: Mapping[str, bool], destination_scope: str = "default") -> dict[str, bool]: ...
    def record_eligible(self, event_fingerprint: str, event_type: str, *, destination_scope: str = "default",
                        signal_id: str | None = None) -> bool: ...
    def reserve(self, event_fingerprint: str, event_type: str, *, destination_scope: str = "default",
                signal_id: str | None = None, lease: timedelta = timedelta(minutes=2)) -> bool: ...
    def mark_sent(self, event_fingerprint: str, *, destination_scope: str = "default") -> None: ...
    def mark_failed(self, event_fingerprint: str, error_code: str,
                    *, destination_scope: str = "default") -> None: ...
    def notification(self, event_fingerprint: str, destination_scope: str = "default") -> Mapping[str, Any] | None: ...
    def has_prior_active(self, setup_fingerprint: str, destination_scope: str = "default") -> bool: ...


class PersistenceStores(tuple):
    __slots__ = ()
    def __new__(cls, signal: SignalStore, scanner: ScannerStateStore, notification: NotificationStore):
        return tuple.__new__(cls, (signal, scanner, notification))
    signal = property(lambda self: self[0])
    scanner = property(lambda self: self[1])
    notification = property(lambda self: self[2])
