from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import create_engine, delete, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.models import Bar, StandardDecision, SymbolAnalysis
from app.parameters import EngineParameters
from app.persistence.contracts import PersistenceStores, ScannerHeartbeat, ScannerState
from app.persistence.schema import (metadata, notification_settings, notifications,
    scanner_heartbeats, scanner_state, signal_outcomes, signal_state_transitions, signals)
from app.signal_identity import signal_fingerprint

DEFAULT_PREFERENCES = {"radar": True, "ready": True, "invalidated": True,
                       "too_late": True, "allow_b_quality": False}
EXPECTED_ALEMBIC_REVISION = "20260815_02"


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


class SQLiteSignalStore(SQLAlchemySignalStore): pass
class SQLiteScannerStateStore(SQLAlchemyScannerStateStore): pass
class SQLiteNotificationStore(SQLAlchemyNotificationStore): pass
class PostgresSignalStore(SQLAlchemySignalStore): pass
class PostgresScannerStateStore(SQLAlchemyScannerStateStore): pass
class PostgresNotificationStore(SQLAlchemyNotificationStore): pass


def create_stores(database_url: str, *, initialize: bool = False, connect_timeout: int = 5,
                  pool_size: int = 5, max_overflow: int = 0, pool_timeout: int = 5,
                  pool_recycle: int = 300) -> PersistenceStores:
    engine = _engine(database_url, connect_timeout, pool_size, max_overflow,
                     pool_timeout, pool_recycle)
    classes = (PostgresSignalStore, PostgresScannerStateStore, PostgresNotificationStore) if database_url.startswith("postgresql") else (
        SQLiteSignalStore, SQLiteScannerStateStore, SQLiteNotificationStore)
    stores = PersistenceStores(*(cls(engine) for cls in classes))
    if initialize: stores.signal.initialize()
    return stores


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve()}"
