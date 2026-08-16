from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Protocol, Sequence

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


@dataclass(frozen=True)
class ScanSymbolCoverage:
    symbol: str
    attempt_status: str
    result_classification: str
    market_timestamp: datetime | None
    decision_bar_timestamp: datetime | None
    provider_timestamp: datetime | None
    data_age_seconds: int | None
    data_status: str
    market_session: str
    candidate_instance_ids: tuple[str, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True)
class ScanCycleCoverage:
    cycle_id: str
    scanner_scope: str
    run_id: str
    started_at: datetime
    completed_at: datetime
    coverage_status: str
    market_session: str
    data_status: str
    expected_symbols: tuple[str, ...]
    scanner_ready_at_start: bool
    scanner_ready_at_end: bool
    symbols: tuple[ScanSymbolCoverage, ...]
    error_code: str | None = None


@dataclass(frozen=True)
class OutcomeAssessment:
    outcome_assessment_id: str
    setup_instance_id: str
    reference_observation_id: str | None
    primary_outcome: str
    outcome_contract_version: str
    calculation_version: str
    evaluation_horizon_id: str
    evidence_granularity: str
    evidence_start_at: datetime
    evidence_cutoff_at: datetime
    computed_at: datetime
    ready_reference_price: float | None = None
    theoretical_entry_price: float | None = None
    entry_reference_type: str = "UNDEFINED"
    initial_invalidation: float | None = None
    initial_risk_1r: float | None = None
    target_1_price: float | None = None
    target_2_price: float | None = None
    target_1_r: float | None = None
    target_2_r: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    terminal_r: float | None = None
    end_of_session_r: float | None = None
    target_hit_at: datetime | None = None
    invalidation_hit_at: datetime | None = None
    time_to_target_seconds: int | None = None
    time_to_invalidation_seconds: int | None = None
    terminal_reason: str | None = None
    ambiguity_reason: str | None = None
    expiration_outcome: str | None = None
    last_evidence_at: datetime | None = None
    evidence: Mapping[str, Any] | None = None
    supersedes_assessment_id: str | None = None


class ResearchLedger(Protocol):
    persistent: bool
    def observe_candidates(self, analysis: SymbolAnalysis, candidates: Sequence[Any],
                           parameters: EngineParameters, *, cycle_id: str | None = None,
                           source_mode: str = "LIVE",
                           replay_run_id: str | None = None) -> list[str]: ...
    def reconcile_symbol(self, symbol: str, *, session_date: date,
                         decision_bar_timestamp: datetime,
                         observed_instance_ids: Sequence[str]) -> None: ...
    def record_data_unavailable(self, symbol: str, *, observed_at: datetime,
                                session_date: date, data_state: str,
                                cycle_id: str | None = None) -> None: ...
    def close_session(self, session_date: date, *, market_timestamp: datetime) -> None: ...
    def record_scan_cycle(self, coverage: ScanCycleCoverage) -> None: ...
    def append_outcome(self, assessment: OutcomeAssessment) -> bool: ...
    def instances(self, symbol: str | None = None) -> list[Mapping[str, Any]]: ...
    def observations(self, setup_instance_id: str) -> list[Mapping[str, Any]]: ...
    def scan_cycle(self, cycle_id: str) -> Mapping[str, Any] | None: ...
    def outcome_assessments(self, setup_instance_id: str) -> list[Mapping[str, Any]]: ...


class PersistenceStores(tuple):
    __slots__ = ()
    def __new__(cls, signal: SignalStore, scanner: ScannerStateStore,
                notification: NotificationStore, research: ResearchLedger):
        return tuple.__new__(cls, (signal, scanner, notification, research))
    signal = property(lambda self: self[0])
    scanner = property(lambda self: self[1])
    notification = property(lambda self: self[2])
    research = property(lambda self: self[3])
