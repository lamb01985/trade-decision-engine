from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import create_engine, delete, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.models import Bar, SetupProgress, StandardDecision, SymbolAnalysis
from app.parameters import EngineParameters
from app.persistence.contracts import (OutcomeAssessment, PersistenceStores,
    ScanCycleCoverage, ScannerHeartbeat, ScannerState)
from app.persistence.schema import (metadata, notification_settings, notifications,
    research_scan_cycles, research_scan_symbol_coverage, scanner_heartbeats,
    scanner_state, setup_instances, setup_observations,
    setup_outcome_assessments, signal_outcomes, signal_state_transitions, signals)
from app.research_identity import (CLOSURE_POLICY_VERSION,
    CONTEXT_CONTRACT_VERSION, COVERAGE_CONTRACT_VERSION,
    FEATURE_DEFINITION_VERSION, IDENTITY_SCHEMA_VERSION,
    MEASUREMENT_CONTRACT_VERSION, OBSERVATION_CONTRACT_VERSION,
    OCCURRENCE_POLICY_VERSION, REGIME_CONTRACT_VERSION,
    SETUP_CATALOG_VERSION, anchor_key, canonical_json, setup_instance_id as make_setup_instance_id,
    sha256_hex, universe_hash)
from app.signal_identity import signal_fingerprint
from app.versioning import ENGINE_VERSION, PARAMETER_VERSION, SETUP_RULES_VERSION

DEFAULT_PREFERENCES = {"radar": True, "ready": True, "invalidated": True,
                       "too_late": True, "allow_b_quality": False}
EXPECTED_ALEMBIC_REVISION = "20260816_03"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    """SQLite drops tzinfo; all persisted timestamps are defined as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _engine(url: str, connect_timeout: int = 5, pool_size: int = 5,
            max_overflow: int = 0, pool_timeout: int = 5,
            pool_recycle: int = 300) -> Engine:
    kwargs: dict[str, Any] = {"pool_pre_ping": True, "pool_recycle": pool_recycle}
    if url.startswith("postgresql"):
        kwargs.update(pool_size=pool_size, max_overflow=max_overflow, pool_timeout=pool_timeout,
                      connect_args={"connect_timeout": connect_timeout})
    else:
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": connect_timeout}
    return create_engine(url, **kwargs)


class SQLAlchemyStore:
    persistent = True
    def __init__(self, engine: Engine) -> None: self.engine = engine
    def initialize(self) -> None: metadata.create_all(self.engine)
    def ping(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                if connection.dialect.name == "postgresql":
                    revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    if revision != EXPECTED_ALEMBIC_REVISION: return False
            return True
        except Exception:
            return False
    def close(self) -> None: self.engine.dispose()


class SQLAlchemySignalStore(SQLAlchemyStore):
    def observe(self, analysis: SymbolAnalysis, bars: list[Bar], parameters: EngineParameters) -> None:
        decision = analysis.decision
        if decision is None or decision.setup_family.value == "NONE": return
        levels = [{"price": item.price, "types": item.types, "importance": item.importance.value,
                   "confluence": item.confluence} for item in analysis.levels]
        fingerprint = self.record_decision(decision, parameter_snapshot=parameters.as_dict(),
                                           evidence={"levels": levels})
        self._update_from_bars(fingerprint, decision, bars)

    def record_decision(self, decision: StandardDecision, *, parameter_snapshot: Mapping[str, Any],
                        evidence: Mapping[str, Any] | None = None) -> str:
        fingerprint = signal_fingerprint(symbol=decision.symbol, setup_family=decision.setup_family.value,
            direction=decision.direction, trigger=decision.trigger, session=decision.timestamp,
            engine_version=decision.engine_version, setup_rules_version=decision.setup_rules_version)
        now = utcnow(); payload = decision.model_dump(mode="json")
        values = dict(id=str(uuid.uuid4()), fingerprint=fingerprint, symbol=decision.symbol,
            setup_family=decision.setup_family.value, direction=decision.direction,
            session_date=decision.timestamp.date(), trigger=decision.trigger,
            current_state=decision.setup_state.value, quality=decision.quality.value,
            action_now=decision.action_now.value, market_price=decision.market_price,
            invalidation=decision.invalidation, target_1=decision.target_1, target_2=decision.target_2,
            structural_risk_reward=decision.structural_risk_reward,
            nearest_support=decision.nearest_support, nearest_resistance=decision.nearest_resistance,
            vwap_state=decision.vwap_state, structure_5m=decision.structure_5m.value,
            structure_15m=decision.structure_15m.value, atr=decision.atr,
            decision_at=decision.timestamp, state_changed_at=decision.state_changed_at,
            last_observed_at=decision.timestamp, engine_version=decision.engine_version,
            setup_rules_version=decision.setup_rules_version, parameter_version=decision.parameter_version,
            decision_json=payload, levels_snapshot=dict((evidence or {}).get("levels", {})) if isinstance((evidence or {}).get("levels"), Mapping) else (evidence or {}).get("levels", []),
            parameter_snapshot=dict(parameter_snapshot), created_at=now, updated_at=now)
        with self.engine.begin() as connection:
            dialect_insert = postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
            current = connection.execute(select(signals).where(signals.c.fingerprint == fingerprint).with_for_update()).mappings().first()
            if current is None:
                inserted = connection.execute(dialect_insert(signals).values(**values).on_conflict_do_nothing(
                    index_elements=[signals.c.fingerprint]))
                if inserted.rowcount == 0:
                    current = connection.execute(select(signals).where(
                        signals.c.fingerprint == fingerprint).with_for_update()).mappings().one()
            accepted = current is None or decision.timestamp > aware(current["last_observed_at"])
            if current is not None and accepted:
                mutable = {key: value for key, value in values.items() if key not in {"id", "fingerprint", "created_at"}}
                connection.execute(update(signals).where(signals.c.id == current["id"]).values(**mutable))
                signal_id = current["id"]
            else:
                signal_id = values["id"] if current is None else current["id"]
            prior_state = current["current_state"] if current else None
            if accepted and prior_state != decision.setup_state.value:
                transition = dict(id=str(uuid.uuid4()), signal_id=signal_id, from_state=prior_state,
                    to_state=decision.setup_state.value, transition_at=decision.state_changed_at,
                    observed_at=decision.timestamp, market_price=decision.market_price,
                    quality=decision.quality.value, action_now=decision.action_now.value,
                    reason_json=decision.reasoning, engine_version=decision.engine_version,
                    setup_rules_version=decision.setup_rules_version, parameter_version=decision.parameter_version,
                    created_at=now)
                transition_insert = dialect_insert(signal_state_transitions).values(**transition)
                transition_insert = (transition_insert.on_conflict_do_nothing(constraint="uq_signal_transition")
                    if connection.dialect.name == "postgresql" else transition_insert.on_conflict_do_nothing(
                        index_elements=[signal_state_transitions.c.signal_id,
                            signal_state_transitions.c.to_state, signal_state_transitions.c.transition_at]))
                connection.execute(transition_insert)
            connection.execute(dialect_insert(signal_outcomes).values(signal_id=signal_id, mfe_price=0, mae_price=0,
                    target_1_hit=False, target_2_hit=False, invalidation_hit=False,
                    calculation_version="price-excursion-v1", created_at=now, updated_at=now).on_conflict_do_nothing(
                        index_elements=[signal_outcomes.c.signal_id]))
        return fingerprint

    def get_latest_decision(self, symbol: str) -> StandardDecision | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(signals.c.decision_json).where(signals.c.symbol == symbol.upper())
                .order_by(signals.c.last_observed_at.desc()).limit(1)).scalar_one_or_none()
        return StandardDecision.model_validate(row) if row else None

    def get_signal(self, fingerprint: str) -> Mapping[str, Any] | None:
        with self.engine.connect() as connection:
            return connection.execute(select(signals).where(signals.c.fingerprint == fingerprint)).mappings().first()

    def transitions(self, fingerprint: str) -> list[Mapping[str, Any]]:
        statement = select(signal_state_transitions).join(signals).where(signals.c.fingerprint == fingerprint).order_by(
            signal_state_transitions.c.transition_at, signal_state_transitions.c.id)
        with self.engine.connect() as connection: return list(connection.execute(statement).mappings())

    def update_outcome(self, fingerprint: str, **values: Any) -> None:
        allowed = {"mfe_price", "mae_price", "target_1_hit", "target_2_hit", "invalidation_hit",
                   "target_1_hit_at", "target_2_hit_at", "invalidation_hit_at", "last_evidence_at"}
        unknown = set(values) - allowed
        if unknown: raise ValueError(f"Unsupported outcome fields: {sorted(unknown)}")
        with self.engine.begin() as connection:
            signal_id = connection.execute(select(signals.c.id).where(signals.c.fingerprint == fingerprint)).scalar_one()
            row = connection.execute(select(signal_outcomes).where(signal_outcomes.c.signal_id == signal_id).with_for_update()).mappings().one()
            merged = dict(values)
            for name in ("mfe_price", "mae_price"):
                if name in merged: merged[name] = max(row[name] or 0, merged[name] or 0)
            for flag in ("target_1_hit", "target_2_hit", "invalidation_hit"):
                if flag in merged: merged[flag] = bool(row[flag] or merged[flag])
            for name in ("target_1_hit_at", "target_2_hit_at", "invalidation_hit_at"):
                if name in merged and row[name] is not None:
                    merged[name] = min(aware(row[name]), aware(merged[name])) if merged[name] is not None else row[name]
            connection.execute(update(signal_outcomes).where(signal_outcomes.c.signal_id == signal_id).values(**merged, updated_at=utcnow()))

    def outcome(self, fingerprint: str) -> Mapping[str, Any] | None:
        with self.engine.connect() as connection:
            return connection.execute(select(signal_outcomes).join(signals).where(signals.c.fingerprint == fingerprint)).mappings().first()

    def _update_from_bars(self, fingerprint: str, decision: StandardDecision, bars: list[Bar]) -> None:
        future = [bar for bar in bars if bar.timestamp > decision.timestamp]
        if not future: return
        favorable = max(bar.high-decision.market_price for bar in future) if decision.direction == "LONG" else max(decision.market_price-bar.low for bar in future)
        adverse = max(decision.market_price-bar.low for bar in future) if decision.direction == "LONG" else max(bar.high-decision.market_price for bar in future)
        def hit(value: float | None, target: bool) -> datetime | None:
            if value is None: return None
            return next((bar.timestamp for bar in future if (bar.high >= value if (decision.direction == "LONG") == target else bar.low <= value)), None)
        t1, t2, inv = hit(decision.target_1, True), hit(decision.target_2, True), hit(decision.invalidation, False)
        self.update_outcome(fingerprint, mfe_price=max(0, favorable), mae_price=max(0, adverse),
            target_1_hit=bool(t1), target_2_hit=bool(t2), invalidation_hit=bool(inv),
            target_1_hit_at=t1, target_2_hit_at=t2, invalidation_hit_at=inv,
            last_evidence_at=max(bar.timestamp for bar in future))


class SQLAlchemyScannerStateStore(SQLAlchemyStore):
    def get(self, scanner_scope: str, policy_fingerprint: str, symbol: str) -> ScannerState | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(scanner_state).where(scanner_state.c.scanner_scope == scanner_scope,
                scanner_state.c.policy_fingerprint == policy_fingerprint, scanner_state.c.symbol == symbol.upper())).mappings().first()
        if not row: return None
        values = {key: row[key] for key in ScannerState.__dataclass_fields__}
        for key in ("last_transition_at", "last_alert_at", "last_observed_at"):
            if values[key] is not None: values[key] = aware(values[key])
        return ScannerState(**values)

    def advance(self, state: ScannerState) -> bool:
        key = (scanner_state.c.scanner_scope == state.scanner_scope,
               scanner_state.c.policy_fingerprint == state.policy_fingerprint,
               scanner_state.c.symbol == state.symbol.upper())
        now = utcnow()
        with self.engine.begin() as connection:
            prior = connection.execute(select(scanner_state).where(*key).with_for_update()).mappings().first()
            if prior and state.last_observed_at <= aware(prior["last_observed_at"]): return False
            values = dict(state.__dict__); values["symbol"] = state.symbol.upper(); values["updated_at"] = now
            if prior: connection.execute(update(scanner_state).where(*key).values(**values))
            else: connection.execute(insert(scanner_state).values(**values, created_at=now))
        return prior is None or prior["state"] != state.state or prior["candidate_fingerprint"] != state.candidate_fingerprint

    def clear(self, scanner_scope: str, policy_fingerprint: str, symbol: str) -> None:
        with self.engine.begin() as connection: connection.execute(delete(scanner_state).where(
            scanner_state.c.scanner_scope == scanner_scope, scanner_state.c.policy_fingerprint == policy_fingerprint,
            scanner_state.c.symbol == symbol.upper()))

    def acquire_cycle(self, scanner_scope: str, owner_id: str, started_at: datetime,
                      lease: timedelta) -> bool:
        now = aware(started_at)
        with self.engine.begin() as connection:
            dialect_insert = postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
            seed = dict(scanner_scope=scanner_scope, enabled=True, running=False, owner_id=None,
                lease_expires_at=None, last_scan_started_at=None, last_scan_completed_at=None,
                last_successful_scan_at=None, last_symbol_count=0, last_error_count=0,
                last_transition_count=0, last_data_status=None, last_error_code=None,
                created_at=now, updated_at=now)
            connection.execute(dialect_insert(scanner_heartbeats).values(**seed).on_conflict_do_nothing(
                index_elements=[scanner_heartbeats.c.scanner_scope]))
            row = connection.execute(select(scanner_heartbeats).where(
                scanner_heartbeats.c.scanner_scope == scanner_scope).with_for_update()).mappings().one()
            lease_active = (row["running"] and row["lease_expires_at"] is not None
                            and aware(row["lease_expires_at"]) > now and row["owner_id"] != owner_id)
            if lease_active:
                return False
            connection.execute(update(scanner_heartbeats).where(
                scanner_heartbeats.c.scanner_scope == scanner_scope).values(enabled=True, running=True,
                owner_id=owner_id, lease_expires_at=now + lease, last_scan_started_at=now,
                last_error_code=None, updated_at=now))
        return True

    def complete_cycle(self, scanner_scope: str, owner_id: str, *, completed_at: datetime,
                       successful: bool, symbol_count: int, error_count: int,
                       transition_count: int, data_status: str | None,
                       error_code: str | None = None) -> None:
        now = aware(completed_at)
        values: dict[str, Any] = dict(running=False, owner_id=None, lease_expires_at=None,
            last_scan_completed_at=now, last_symbol_count=max(0, symbol_count),
            last_error_count=max(0, error_count), last_transition_count=max(0, transition_count),
            last_data_status=data_status, last_error_code=error_code[:128] if error_code else None,
            updated_at=now)
        if successful:
            values["last_successful_scan_at"] = now
        with self.engine.begin() as connection:
            result = connection.execute(update(scanner_heartbeats).where(
                scanner_heartbeats.c.scanner_scope == scanner_scope,
                scanner_heartbeats.c.owner_id == owner_id).values(**values))
            if result.rowcount != 1:
                raise RuntimeError("Scanner cycle lease is no longer owned by this process")

    def heartbeat(self, scanner_scope: str) -> ScannerHeartbeat | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(scanner_heartbeats).where(
                scanner_heartbeats.c.scanner_scope == scanner_scope)).mappings().first()
        if row is None:
            return None
        values = {key: row[key] for key in ScannerHeartbeat.__dataclass_fields__}
        for key in ("lease_expires_at", "last_scan_started_at", "last_scan_completed_at",
                    "last_successful_scan_at", "updated_at"):
            if values[key] is not None:
                values[key] = aware(values[key])
        return ScannerHeartbeat(**values)


class SQLAlchemyNotificationStore(SQLAlchemyStore):
    def preferences(self, destination_scope: str = "default") -> dict[str, bool]:
        with self.engine.begin() as connection:
            row = connection.execute(select(notification_settings).where(notification_settings.c.destination_scope == destination_scope)).mappings().first()
            if row is None:
                now=utcnow(); connection.execute(insert(notification_settings).values(destination_scope=destination_scope, **DEFAULT_PREFERENCES, created_at=now, updated_at=now))
                return dict(DEFAULT_PREFERENCES)
        return {key: bool(row[key]) for key in DEFAULT_PREFERENCES}

    def update_preferences(self, values: Mapping[str, bool], destination_scope: str = "default") -> dict[str, bool]:
        current = self.preferences(destination_scope); current.update({key: bool(value) for key, value in values.items() if key in current})
        with self.engine.begin() as connection: connection.execute(update(notification_settings).where(
            notification_settings.c.destination_scope == destination_scope).values(**current, updated_at=utcnow()))
        return current

    def record_eligible(self, event_fingerprint: str, event_type: str, *, destination_scope: str = "default",
                        signal_id: str | None = None) -> bool:
        now = utcnow()
        values = dict(id=str(uuid.uuid4()), signal_id=signal_id, channel="PUSHOVER",
            destination_scope=destination_scope, event_type=event_type,
            event_fingerprint=event_fingerprint, status="ELIGIBLE", attempt_count=0,
            claimed_at=None, claim_expires_at=None, first_attempt_at=None, last_attempt_at=None,
            delivered_at=None, error_code=None, created_at=now, updated_at=now)
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(notifications).values(**values))
            return True
        except IntegrityError:
            return False

    def reserve(self, event_fingerprint: str, event_type: str, *, destination_scope: str = "default",
                signal_id: str | None = None, lease: timedelta = timedelta(minutes=2)) -> bool:
        now=utcnow(); values=dict(id=str(uuid.uuid4()), signal_id=signal_id, channel="PUSHOVER",
            destination_scope=destination_scope, event_type=event_type, event_fingerprint=event_fingerprint,
            status="CLAIMED", attempt_count=1, claimed_at=now, claim_expires_at=now+lease,
            first_attempt_at=now, last_attempt_at=now, created_at=now, updated_at=now)
        try:
            with self.engine.begin() as connection:
                promoted = connection.execute(update(notifications).where(
                    notifications.c.channel == "PUSHOVER",
                    notifications.c.destination_scope == destination_scope,
                    notifications.c.event_fingerprint == event_fingerprint,
                    notifications.c.status == "ELIGIBLE").values(status="CLAIMED", attempt_count=1,
                        claimed_at=now, claim_expires_at=now+lease, first_attempt_at=now,
                        last_attempt_at=now, updated_at=now))
                if promoted.rowcount == 1:
                    return True
                connection.execute(insert(notifications).values(**values))
            return True
        except IntegrityError:
            return False

    def mark_sent(self, event_fingerprint: str, *, destination_scope: str = "default") -> None:
        self._complete(event_fingerprint, destination_scope, "SENT", None)
    def mark_failed(self, event_fingerprint: str, error_code: str, *, destination_scope: str = "default") -> None:
        self._complete(event_fingerprint, destination_scope, "FAILED", error_code[:128])
    def _complete(self, fingerprint: str, scope: str, status: str, error: str | None) -> None:
        now=utcnow()
        with self.engine.begin() as connection: connection.execute(update(notifications).where(
            notifications.c.channel == "PUSHOVER", notifications.c.destination_scope == scope,
            notifications.c.event_fingerprint == fingerprint).values(status=status, delivered_at=now if status == "SENT" else None,
                error_code=error, updated_at=now))
    def notification(self, event_fingerprint: str, destination_scope: str = "default") -> Mapping[str, Any] | None:
        with self.engine.connect() as connection: return connection.execute(select(notifications).where(
            notifications.c.channel == "PUSHOVER", notifications.c.destination_scope == destination_scope,
            notifications.c.event_fingerprint == event_fingerprint)).mappings().first()
    def has_prior_active(self, setup_fingerprint: str, destination_scope: str = "default") -> bool:
        keys = (f"{setup_fingerprint}:RADAR", f"{setup_fingerprint}:READY")
        with self.engine.connect() as connection:
            return connection.execute(select(notifications.c.id).where(
                notifications.c.channel == "PUSHOVER",
                notifications.c.destination_scope == destination_scope,
                notifications.c.event_fingerprint.in_(keys)).limit(1)).first() is not None


TERMINAL_CLOSURES = {"INVALIDATED", "EXPIRED", "CONDITION_NO_LONGER_PRESENT", "SESSION_ENDED"}
OUTCOME_VALUES = {"TARGET_FIRST", "INVALIDATION_FIRST", "NEITHER", "AMBIGUOUS"}


def _recommended_action(state: SetupProgress) -> str:
    if state in {SetupProgress.FORMING, SetupProgress.WATCHING}:
        return "MONITOR"
    if state == SetupProgress.TRIGGERED:
        return "WAIT_FOR_CONFIRMATION"
    if state == SetupProgress.CONFIRMED:
        return "READY"
    if state == SetupProgress.INVALIDATED:
        return "INVALIDATED"
    if state == SetupProgress.TOO_LATE:
        return "EXPIRED"
    return "DATA_UNAVAILABLE"


def _minutes_since_open(timestamp: datetime) -> int | None:
    from app.engines.market import ET
    local = aware(timestamp).astimezone(ET)
    opened = datetime.combine(local.date(), time(9, 30), tzinfo=ET)
    minutes = int((local - opened).total_seconds() // 60)
    return minutes if minutes >= 0 else None


class SQLAlchemyResearchLedger(SQLAlchemyStore):
    """Append-only research adapter downstream of the frozen setup detector."""

    def _dialect_insert(self, connection):
        return postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert

    def _latest_observation(self, connection, instance_id: str):
        return connection.execute(select(setup_observations).where(
            setup_observations.c.setup_instance_id == instance_id).order_by(
                setup_observations.c.market_timestamp.desc(),
                setup_observations.c.created_at.desc()).limit(1)).mappings().first()

    def _active_instance(self, connection, *, symbol: str, session_date: date,
                         family: str, direction: str, anchor_digest: str,
                         engine_version: str, setup_rules_version: str,
                         parameter_version: str):
        rows = connection.execute(select(setup_instances).where(
            setup_instances.c.symbol == symbol.upper(),
            setup_instances.c.session_date == session_date,
            setup_instances.c.setup_family == family,
            setup_instances.c.direction == direction,
            setup_instances.c.anchor_key == anchor_digest,
            setup_instances.c.engine_version == engine_version,
            setup_instances.c.setup_rules_version == setup_rules_version,
            setup_instances.c.parameter_version == parameter_version,
        ).order_by(setup_instances.c.episode_started_at.desc())).mappings()
        for row in rows:
            latest = self._latest_observation(connection, row["setup_instance_id"])
            if latest is None or latest["closure_reason"] not in TERMINAL_CLOSURES:
                return row
        return None

    @staticmethod
    def _selected(analysis: SymbolAnalysis, candidate) -> bool:
        decision = analysis.decision
        return bool(decision and decision.setup_family == candidate.family
                    and decision.direction == candidate.direction
                    and decision.trigger is not None
                    and abs(decision.trigger - candidate.trigger) < 0.0001)

    @staticmethod
    def _levels(analysis: SymbolAnalysis) -> list[dict[str, Any]]:
        return [{"price": item.price, "types": list(item.types),
                 "importance": item.importance.value, "distance": item.distance,
                 "distance_pct": item.distance_pct, "position": item.position,
                 "confluence": item.confluence} for item in analysis.levels]

    @staticmethod
    def _market_context(analysis: SymbolAnalysis) -> dict[str, Any]:
        instrument = analysis.instrument
        frames = {name: frame.model_dump(mode="json") for name, frame in instrument.timeframes.items()}
        return {
            "symbol_context": {
                "as_of": instrument.as_of.isoformat() if instrument.as_of else None,
                "timeframes": frames,
                "high_of_day": instrument.high_of_day, "low_of_day": instrument.low_of_day,
                "prior_day_high": instrument.prior_day_high,
                "prior_day_low": instrument.prior_day_low,
                "prior_day_close": instrument.prior_day_close,
                "opening_range_high": instrument.opening_range_high,
                "opening_range_low": instrument.opening_range_low,
                "premarket_high": instrument.premarket_high,
                "premarket_low": instrument.premarket_low,
                "change_pct": instrument.change_pct,
                "average_dollar_volume": instrument.average_dollar_volume,
                "atr_5m": instrument.atr_5m,
            },
            "core_indexes": {symbol: {"availability": "UNAVAILABLE", "reason": "NOT_SYNCHRONIZED_V1"}
                             for symbol in ("SPY", "QQQ", "IWM")},
            "vix": {"availability": "UNAVAILABLE", "reason": "SOURCE_NOT_VALIDATED"},
            "nq": {"availability": "UNAVAILABLE", "reason": "SOURCE_NOT_VALIDATED"},
            "sector": {"availability": "UNAVAILABLE", "reason": "MAPPING_NOT_CONFIGURED"},
            "breadth": {"availability": "UNAVAILABLE", "reason": "NOT_IN_CONTEXT_V1"},
            "options_gex": {"availability": "NOT_APPLICABLE", "reason": "EXPERIMENTAL_EXCLUDED"},
            "regime": {"trend": "UNAVAILABLE", "volatility_level": "UNAVAILABLE",
                       "volatility_change": "UNAVAILABLE"},
        }

    def observe_candidates(self, analysis: SymbolAnalysis, candidates,
                           parameters: EngineParameters, *, cycle_id: str | None = None,
                           source_mode: str = "LIVE",
                           replay_run_id: str | None = None) -> list[str]:
        if source_mode not in {"LIVE", "REPLAY"}:
            raise ValueError("Research source_mode must be LIVE or REPLAY")
        if source_mode == "REPLAY" and not replay_run_id:
            raise ValueError("Replay observations require replay_run_id")
        decision_bar = analysis.decision_bar_timestamp or analysis.instrument.as_of
        observed_at = analysis.analysis_timestamp or utcnow()
        if decision_bar is None:
            raise ValueError("Research observations require a completed decision bar timestamp")
        if analysis.instrument.as_of is not None and aware(analysis.instrument.as_of) > aware(decision_bar):
            raise ValueError("Point-in-time features cannot be newer than the completed decision bar")
        from app.engines.market import ET
        session_day = aware(decision_bar).astimezone(ET).date()
        parameter_snapshot = parameters.as_dict()
        parameter_hash = hashlib_snapshot(parameter_snapshot)
        result: list[str] = []
        with self.engine.begin() as connection:
            dialect_insert = self._dialect_insert(connection)
            for candidate in candidates:
                first_qualifying_bar = candidate.qualifying_bar_at or decision_bar
                if aware(first_qualifying_bar) > aware(decision_bar):
                    raise ValueError("Candidate qualification cannot be newer than the decision bar")
                family = candidate.family.value
                anchor_payload = dict(candidate.anchor)
                if candidate.anchor_type.startswith("OPENING_RANGE") or candidate.anchor_type.startswith("SESSION_VWAP"):
                    anchor_payload["session_date"] = session_day.isoformat()
                anchor_digest = anchor_key(candidate.anchor_type, anchor_payload)
                selected = self._selected(analysis, candidate)
                decision = analysis.decision if selected else None
                state = (decision.setup_state if decision and decision.setup_state in {
                    SetupProgress.TOO_LATE, SetupProgress.INVALIDATED}
                         else candidate.state)
                active = self._active_instance(connection, symbol=analysis.instrument.symbol,
                    session_date=session_day, family=family, direction=candidate.direction,
                    anchor_digest=anchor_digest, engine_version=ENGINE_VERSION,
                    setup_rules_version=SETUP_RULES_VERSION, parameter_version=PARAMETER_VERSION)
                if active is None:
                    instance_id = make_setup_instance_id(symbol=analysis.instrument.symbol,
                        session_date=session_day, setup_family=family, direction=candidate.direction,
                        family_anchor_type=candidate.anchor_type, family_anchor=anchor_payload,
                        first_qualifying_bar_at=first_qualifying_bar, engine_version=ENGINE_VERSION,
                        setup_rules_version=SETUP_RULES_VERSION, parameter_version=PARAMETER_VERSION)
                    source_fingerprint = (signal_fingerprint(symbol=decision.symbol,
                        setup_family=family, direction=candidate.direction, trigger=candidate.trigger,
                        session=decision_bar, engine_version=ENGINE_VERSION,
                        setup_rules_version=SETUP_RULES_VERSION) if selected and decision else None)
                    instance_values = dict(setup_instance_id=instance_id,
                        identity_schema_version=IDENTITY_SCHEMA_VERSION,
                        occurrence_policy_version=OCCURRENCE_POLICY_VERSION,
                        measurement_contract_version=MEASUREMENT_CONTRACT_VERSION,
                        symbol=analysis.instrument.symbol.upper(), session_date=session_day,
                        setup_family=family, direction=candidate.direction,
                        family_anchor_type=candidate.anchor_type, anchor_key=anchor_digest,
                        family_anchor_json=anchor_payload, episode_started_at=first_qualifying_bar,
                        first_observed_at=observed_at, source_signal_fingerprint=source_fingerprint,
                        engine_version=ENGINE_VERSION, setup_rules_version=SETUP_RULES_VERSION,
                        parameter_version=PARAMETER_VERSION, parameter_snapshot_hash=parameter_hash,
                        setup_catalog_version=SETUP_CATALOG_VERSION,
                        research_status="PRODUCTION", actionable_allowed=True, created_at=utcnow())
                    inserted = connection.execute(
                        dialect_insert(setup_instances).values(**instance_values).on_conflict_do_nothing())
                    if inserted.rowcount == 0:
                        stored = connection.execute(select(setup_instances.c.setup_instance_id).where(
                            setup_instances.c.symbol == analysis.instrument.symbol.upper(),
                            setup_instances.c.session_date == session_day,
                            setup_instances.c.setup_family == family,
                            setup_instances.c.direction == candidate.direction,
                            setup_instances.c.anchor_key == anchor_digest,
                            setup_instances.c.episode_started_at == first_qualifying_bar,
                            setup_instances.c.engine_version == ENGINE_VERSION,
                            setup_instances.c.setup_rules_version == SETUP_RULES_VERSION,
                            setup_instances.c.parameter_version == PARAMETER_VERSION,
                        )).scalar_one()
                        instance_id = stored
                else:
                    instance_id = active["setup_instance_id"]

                confirmation = [
                    {"id": sha256_hex({"kind": "evidence", "label": item})[:16],
                     "label": item, "state": "PASS", "required": True}
                    for item in candidate.reasoning
                ] + [
                    {"id": sha256_hex({"kind": "missing", "label": item})[:16],
                     "label": item, "state": "PENDING", "required": True}
                    for item in candidate.missing
                ]
                plan = decision if selected else None
                invalidation = plan.invalidation if plan else None
                initial_risk = (abs(candidate.trigger - invalidation)
                                if invalidation is not None and candidate.trigger != invalidation else None)
                data_quality = {"data_status": analysis.data_status, "data_state": analysis.data_state,
                    "market_session": analysis.market_session, "provider_timestamp": (
                        analysis.provider_timestamp.isoformat() if analysis.provider_timestamp else None),
                    "analysis_timestamp": observed_at.isoformat(), "data_age_seconds": analysis.data_age_seconds,
                    "decision_bar_timestamp": decision_bar.isoformat(), "missing_fields": [],
                    "source_mode": source_mode, "replay_run_id": replay_run_id}
                semantic_payload = {"state": state.value, "action": _recommended_action(state),
                    "trigger": candidate.trigger, "invalidation": invalidation,
                    "target_1": plan.target_1 if plan else None, "target_2": plan.target_2 if plan else None,
                    "confirmation": confirmation, "missing": list(candidate.missing),
                    "data_state": analysis.data_state, "versions": [ENGINE_VERSION,
                        SETUP_RULES_VERSION, PARAMETER_VERSION, OBSERVATION_CONTRACT_VERSION]}
                semantic_digest = sha256_hex(semantic_payload)
                latest = self._latest_observation(connection, instance_id)
                if latest is not None and latest["event_type"] == "CANDIDATE" \
                        and latest["semantic_fingerprint"] == semantic_digest:
                    result.append(instance_id)
                    continue
                event_type = ("CLOSURE" if state in {
                    SetupProgress.TOO_LATE, SetupProgress.INVALIDATED} else "CANDIDATE")
                closure_reason = ("EXPIRED" if state == SetupProgress.TOO_LATE else
                    "INVALIDATED" if state == SetupProgress.INVALIDATED else None)
                observation_fingerprint = sha256_hex({"instance": instance_id,
                    "semantic": semantic_digest, "market_timestamp": decision_bar,
                    "event_type": event_type})
                levels = self._levels(analysis)
                values = dict(observation_id=str(uuid.uuid4()), setup_instance_id=instance_id,
                    cycle_id=cycle_id, observation_fingerprint=observation_fingerprint,
                    semantic_fingerprint=semantic_digest, event_type=event_type,
                    closure_reason=closure_reason,
                    observed_at=observed_at, market_timestamp=decision_bar,
                    decision_bar_timestamp=decision_bar, feature_source_at=decision_bar,
                    market_session=analysis.market_session,
                    minutes_since_regular_open=_minutes_since_open(decision_bar),
                    day_of_week=aware(decision_bar).astimezone(ET).weekday(),
                    setup_family=family, direction=candidate.direction, setup_state=state.value,
                    recommended_action=_recommended_action(state),
                    quality=plan.quality.value if plan else None, score=analysis.setup_score.score,
                    rank_in_scan=None, universe_size=None,
                    qualifying_candidate_count=len(candidates),
                    decision_reference_price=analysis.instrument.timeframes["1m"].price,
                    trigger=candidate.trigger, decision_zone_lower=candidate.trigger,
                    decision_zone_upper=candidate.trigger, invalidation=invalidation,
                    target_1=plan.target_1 if plan else None, target_2=plan.target_2 if plan else None,
                    initial_risk=initial_risk,
                    initial_risk_reward=plan.structural_risk_reward if plan else None,
                    nearest_support=plan.nearest_support if plan else None,
                    nearest_resistance=plan.nearest_resistance if plan else None,
                    score_factors=[item.model_dump(mode="json") for item in analysis.setup_score.factors],
                    levels_snapshot=levels, confirmation_components=confirmation,
                    missing_confirmation=list(candidate.missing),
                    market_context=self._market_context(analysis), data_quality=data_quality,
                    feature_manifest={"available": ["symbol_context", "levels", "score_factors",
                        "candidate_state", "candidate_anchor"],
                        "unavailable": ["synchronized_indexes", "vix", "nq", "sector", "breadth"],
                        "not_applicable": ["options_gex"], "candidate_anchor": anchor_payload,
                        "source_mode": source_mode, "replay_run_id": replay_run_id},
                    parameter_snapshot=parameter_snapshot, engine_version=ENGINE_VERSION,
                    setup_rules_version=SETUP_RULES_VERSION, parameter_version=PARAMETER_VERSION,
                    parameter_snapshot_hash=parameter_hash,
                    observation_contract_version=OBSERVATION_CONTRACT_VERSION,
                    feature_definition_version=FEATURE_DEFINITION_VERSION,
                    context_contract_version=CONTEXT_CONTRACT_VERSION,
                    regime_contract_version=REGIME_CONTRACT_VERSION,
                    closure_policy_version=CLOSURE_POLICY_VERSION,
                    setup_catalog_version=SETUP_CATALOG_VERSION, research_status="PRODUCTION",
                    actionable_allowed=True, created_at=utcnow())
                connection.execute(dialect_insert(setup_observations).values(**values).on_conflict_do_nothing(
                    index_elements=[setup_observations.c.observation_fingerprint]))
                result.append(instance_id)
        return list(dict.fromkeys(result))

    def _append_derived_event(self, connection, source, *, event_type: str,
                              market_timestamp: datetime, closure_reason: str | None,
                              data_state: str | None = None, cycle_id: str | None = None) -> None:
        semantic = sha256_hex({"event_type": event_type, "closure_reason": closure_reason,
            "data_state": data_state, "prior_semantic": source["semantic_fingerprint"]})
        fingerprint = sha256_hex({"instance": source["setup_instance_id"], "semantic": semantic,
            "market_timestamp": market_timestamp, "event_type": event_type})
        values = {key: source[key] for key in setup_observations.c.keys()
                  if key not in {"observation_id", "observation_fingerprint", "semantic_fingerprint",
                                 "event_type", "closure_reason", "observed_at", "market_timestamp",
                                 "cycle_id", "created_at"}}
        replay = (source.get("data_quality") or {}).get("source_mode") == "REPLAY"
        observed_at = market_timestamp + timedelta(minutes=1) if replay else utcnow()
        values.update(observation_id=str(uuid.uuid4()), cycle_id=cycle_id,
            observation_fingerprint=fingerprint, semantic_fingerprint=semantic,
            event_type=event_type, closure_reason=closure_reason, observed_at=observed_at,
            market_timestamp=market_timestamp, created_at=utcnow())
        if data_state is not None:
            quality = dict(values["data_quality"])
            quality["data_state"] = data_state
            values["data_quality"] = quality
            values["recommended_action"] = "DATA_UNAVAILABLE"
        dialect_insert = self._dialect_insert(connection)
        connection.execute(dialect_insert(setup_observations).values(**values).on_conflict_do_nothing(
            index_elements=[setup_observations.c.observation_fingerprint]))

    def reconcile_symbol(self, symbol: str, *, session_date: date,
                         decision_bar_timestamp: datetime,
                         observed_instance_ids) -> None:
        observed = set(observed_instance_ids)
        with self.engine.begin() as connection:
            rows = connection.execute(select(setup_instances).where(
                setup_instances.c.symbol == symbol.upper(),
                setup_instances.c.session_date == session_date)).mappings()
            for instance in rows:
                instance_id = instance["setup_instance_id"]
                latest = self._latest_observation(connection, instance_id)
                if latest is None or latest["closure_reason"] in TERMINAL_CLOSURES:
                    continue
                if instance_id in observed:
                    continue
                if latest["event_type"] == "ABSENCE_PENDING":
                    if aware(latest["market_timestamp"]) == aware(decision_bar_timestamp):
                        continue
                    self._append_derived_event(connection, latest, event_type="CLOSURE",
                        market_timestamp=decision_bar_timestamp,
                        closure_reason="CONDITION_NO_LONGER_PRESENT")
                else:
                    self._append_derived_event(connection, latest, event_type="ABSENCE_PENDING",
                        market_timestamp=decision_bar_timestamp, closure_reason=None)

    def record_data_unavailable(self, symbol: str, *, observed_at: datetime,
                                session_date: date, data_state: str,
                                cycle_id: str | None = None) -> None:
        with self.engine.begin() as connection:
            rows = connection.execute(select(setup_instances).where(
                setup_instances.c.symbol == symbol.upper(),
                setup_instances.c.session_date == session_date).order_by(
                    setup_instances.c.episode_started_at.desc())).mappings()
            for instance in rows:
                latest = self._latest_observation(connection, instance["setup_instance_id"])
                if latest is None or latest["closure_reason"] in TERMINAL_CLOSURES:
                    continue
                if latest["event_type"] == "DATA_UNAVAILABLE" \
                        and latest["data_quality"].get("data_state") == data_state:
                    continue
                self._append_derived_event(connection, latest, event_type="DATA_UNAVAILABLE",
                    market_timestamp=observed_at, closure_reason=None,
                    data_state=data_state, cycle_id=cycle_id)

    def close_session(self, session_date: date, *, market_timestamp: datetime) -> None:
        """Close remaining intraday research episodes at the deterministic RTH boundary."""
        with self.engine.begin() as connection:
            rows = connection.execute(select(setup_instances).where(
                setup_instances.c.session_date == session_date)).mappings()
            for instance in rows:
                latest = self._latest_observation(connection, instance["setup_instance_id"])
                if latest is not None and latest["closure_reason"] not in TERMINAL_CLOSURES:
                    self._append_derived_event(connection, latest, event_type="CLOSURE",
                        market_timestamp=market_timestamp, closure_reason="SESSION_ENDED")

    def record_scan_cycle(self, coverage: ScanCycleCoverage) -> None:
        expected = tuple(sorted({symbol.upper() for symbol in coverage.expected_symbols}))
        children = {item.symbol.upper(): item for item in coverage.symbols}
        if set(children) != set(expected):
            raise ValueError("Scan coverage must contain exactly one fact for every expected symbol")
        attempted = sum(item.attempt_status != "NOT_ATTEMPTED" for item in children.values())
        succeeded = sum(item.attempt_status == "SUCCESS" for item in children.values())
        failed = sum(item.attempt_status not in {"SUCCESS", "NOT_ATTEMPTED"} for item in children.values())
        not_attempted = len(expected) - attempted
        provider_times = [aware(item.provider_timestamp) for item in children.values()
                          if item.provider_timestamp is not None]
        ages = [item.data_age_seconds for item in children.values() if item.data_age_seconds is not None]
        now = utcnow()
        cycle_values = dict(cycle_id=coverage.cycle_id, scanner_scope=coverage.scanner_scope,
            run_id=coverage.run_id, started_at=coverage.started_at, completed_at=coverage.completed_at,
            coverage_status=coverage.coverage_status, market_session=coverage.market_session,
            data_status=coverage.data_status, expected_universe_count=len(expected),
            universe_hash=universe_hash(expected, "hosted-universe-v1"),
            universe_version="hosted-universe-v1", attempted_count=attempted,
            succeeded_count=succeeded, failed_count=failed, not_attempted_count=not_attempted,
            earliest_provider_timestamp=min(provider_times) if provider_times else None,
            latest_provider_timestamp=max(provider_times) if provider_times else None,
            maximum_data_age_seconds=max(ages) if ages else None,
            scanner_ready_at_start=coverage.scanner_ready_at_start,
            scanner_ready_at_end=coverage.scanner_ready_at_end,
            engine_version=ENGINE_VERSION, setup_rules_version=SETUP_RULES_VERSION,
            parameter_version=PARAMETER_VERSION, scanner_config_version="hosted-scanner-v1",
            occurrence_policy_version=OCCURRENCE_POLICY_VERSION,
            measurement_contract_version=MEASUREMENT_CONTRACT_VERSION,
            coverage_contract_version=COVERAGE_CONTRACT_VERSION,
            error_code=coverage.error_code[:128] if coverage.error_code else None, created_at=now)
        with self.engine.begin() as connection:
            dialect_insert = self._dialect_insert(connection)
            connection.execute(dialect_insert(research_scan_cycles).values(**cycle_values).on_conflict_do_nothing(
                index_elements=[research_scan_cycles.c.cycle_id]))
            for symbol in expected:
                item = children[symbol]
                values = dict(cycle_id=coverage.cycle_id, symbol=symbol,
                    attempt_status=item.attempt_status,
                    result_classification=item.result_classification,
                    market_timestamp=item.market_timestamp,
                    decision_bar_timestamp=item.decision_bar_timestamp,
                    provider_timestamp=item.provider_timestamp, data_age_seconds=item.data_age_seconds,
                    data_status=item.data_status, market_session=item.market_session,
                    candidate_count=len(item.candidate_instance_ids),
                    candidate_instance_ids=list(item.candidate_instance_ids),
                    error_code=item.error_code[:128] if item.error_code else None, created_at=now)
                connection.execute(dialect_insert(research_scan_symbol_coverage).values(**values).on_conflict_do_nothing(
                    index_elements=[research_scan_symbol_coverage.c.cycle_id,
                                    research_scan_symbol_coverage.c.symbol]))

    def append_outcome(self, assessment: OutcomeAssessment) -> bool:
        if assessment.primary_outcome not in OUTCOME_VALUES:
            raise ValueError("Unsupported primary outcome")
        if aware(assessment.evidence_cutoff_at) < aware(assessment.evidence_start_at):
            raise ValueError("Outcome evidence cutoff precedes its start")
        values = dict(assessment.__dict__)
        values["evidence_json"] = dict(values.pop("evidence") or {})
        values["created_at"] = utcnow()
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(setup_outcome_assessments).values(**values))
            return True
        except IntegrityError:
            return False

    def instances(self, symbol: str | None = None) -> list[Mapping[str, Any]]:
        statement = select(setup_instances).order_by(setup_instances.c.episode_started_at)
        if symbol:
            statement = statement.where(setup_instances.c.symbol == symbol.upper())
        with self.engine.connect() as connection:
            return list(connection.execute(statement).mappings())

    def observations(self, setup_instance_id: str) -> list[Mapping[str, Any]]:
        with self.engine.connect() as connection:
            return list(connection.execute(select(setup_observations).where(
                setup_observations.c.setup_instance_id == setup_instance_id).order_by(
                    setup_observations.c.market_timestamp, setup_observations.c.created_at)).mappings())

    def scan_cycle(self, cycle_id: str) -> Mapping[str, Any] | None:
        with self.engine.connect() as connection:
            return connection.execute(select(research_scan_cycles).where(
                research_scan_cycles.c.cycle_id == cycle_id)).mappings().first()

    def outcome_assessments(self, setup_instance_id: str) -> list[Mapping[str, Any]]:
        with self.engine.connect() as connection:
            return list(connection.execute(select(setup_outcome_assessments).where(
                setup_outcome_assessments.c.setup_instance_id == setup_instance_id).order_by(
                    setup_outcome_assessments.c.computed_at)).mappings())


def hashlib_snapshot(snapshot: Mapping[str, Any]) -> str:
    # Parameter hashes retain the complete numeric representation; identity
    # canonicalization rounds only price anchors per the measurement contract.
    return __import__("hashlib").sha256(json_bytes(snapshot)).hexdigest()


def json_bytes(value: Mapping[str, Any]) -> bytes:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


class SQLiteSignalStore(SQLAlchemySignalStore): pass
class SQLiteScannerStateStore(SQLAlchemyScannerStateStore): pass
class SQLiteNotificationStore(SQLAlchemyNotificationStore): pass
class SQLiteResearchLedger(SQLAlchemyResearchLedger): pass
class PostgresSignalStore(SQLAlchemySignalStore): pass
class PostgresScannerStateStore(SQLAlchemyScannerStateStore): pass
class PostgresNotificationStore(SQLAlchemyNotificationStore): pass
class PostgresResearchLedger(SQLAlchemyResearchLedger): pass


def create_stores(database_url: str, *, initialize: bool = False, connect_timeout: int = 5,
                  pool_size: int = 5, max_overflow: int = 0, pool_timeout: int = 5,
                  pool_recycle: int = 300) -> PersistenceStores:
    engine = _engine(database_url, connect_timeout, pool_size, max_overflow,
                     pool_timeout, pool_recycle)
    classes = (PostgresSignalStore, PostgresScannerStateStore, PostgresNotificationStore,
               PostgresResearchLedger) if database_url.startswith("postgresql") else (
        SQLiteSignalStore, SQLiteScannerStateStore, SQLiteNotificationStore,
        SQLiteResearchLedger)
    stores = PersistenceStores(*(cls(engine) for cls in classes))
    if initialize: stores.signal.initialize()
    return stores


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve()}"
