from __future__ import annotations

from sqlalchemy import (JSON, Boolean, CheckConstraint, Column, Date, DateTime, Float,
                        ForeignKey, Index, Integer, MetaData, String, Table, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB

DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

metadata = MetaData()

signals = Table("signals", metadata,
    Column("id", String(36), primary_key=True), Column("fingerprint", String(64), nullable=False, unique=True),
    Column("symbol", String(16), nullable=False), Column("setup_family", String(64), nullable=False),
    Column("direction", String(8), nullable=False), Column("session_date", Date, nullable=False),
    Column("trigger", Float), Column("current_state", String(32), nullable=False),
    Column("quality", String(16), nullable=False), Column("action_now", String(32), nullable=False),
    Column("market_price", Float, nullable=False), Column("invalidation", Float), Column("target_1", Float),
    Column("target_2", Float), Column("structural_risk_reward", Float), Column("nearest_support", Float),
    Column("nearest_resistance", Float), Column("vwap_state", String(16), nullable=False),
    Column("structure_5m", String(16), nullable=False), Column("structure_15m", String(16), nullable=False),
    Column("atr", Float), Column("decision_at", DateTime(timezone=True), nullable=False),
    Column("state_changed_at", DateTime(timezone=True), nullable=False),
    Column("last_observed_at", DateTime(timezone=True), nullable=False),
    Column("engine_version", String(32), nullable=False), Column("setup_rules_version", String(32), nullable=False),
    Column("parameter_version", String(32), nullable=False), Column("decision_json", DOCUMENT, nullable=False),
    Column("levels_snapshot", DOCUMENT, nullable=False), Column("parameter_snapshot", DOCUMENT, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("direction IN ('LONG','SHORT','NEUTRAL')", name="ck_signals_direction"))
Index("ix_signals_symbol_session", signals.c.symbol, signals.c.session_date)
Index("ix_signals_state_observed", signals.c.current_state, signals.c.last_observed_at)

signal_state_transitions = Table("signal_state_transitions", metadata,
    Column("id", String(36), primary_key=True), Column("signal_id", String(36), ForeignKey("signals.id"), nullable=False),
    Column("from_state", String(32)), Column("to_state", String(32), nullable=False),
    Column("transition_at", DateTime(timezone=True), nullable=False), Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("market_price", Float), Column("quality", String(16)), Column("action_now", String(32)),
    Column("reason_json", DOCUMENT, nullable=False), Column("engine_version", String(32), nullable=False),
    Column("setup_rules_version", String(32), nullable=False), Column("parameter_version", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("signal_id", "to_state", "transition_at", name="uq_signal_transition"))
Index("ix_transitions_signal_time", signal_state_transitions.c.signal_id, signal_state_transitions.c.transition_at)

signal_outcomes = Table("signal_outcomes", metadata,
    Column("signal_id", String(36), ForeignKey("signals.id"), primary_key=True),
    Column("mfe_price", Float, nullable=False, default=0), Column("mae_price", Float, nullable=False, default=0),
    Column("target_1_hit", Boolean, nullable=False, default=False), Column("target_2_hit", Boolean, nullable=False, default=False),
    Column("invalidation_hit", Boolean, nullable=False, default=False), Column("target_1_hit_at", DateTime(timezone=True)),
    Column("target_2_hit_at", DateTime(timezone=True)), Column("invalidation_hit_at", DateTime(timezone=True)),
    Column("last_evidence_at", DateTime(timezone=True)), Column("calculation_version", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))

scanner_state = Table("scanner_state", metadata,
    Column("scanner_scope", String(64), primary_key=True), Column("policy_fingerprint", String(128), primary_key=True),
    Column("symbol", String(16), primary_key=True), Column("signal_id", String(36), ForeignKey("signals.id")),
    Column("state", String(32), nullable=False), Column("candidate_fingerprint", Text, nullable=False),
    Column("last_transition_at", DateTime(timezone=True), nullable=False), Column("last_alert_at", DateTime(timezone=True)),
    Column("last_price", Float, nullable=False), Column("last_score", Integer, nullable=False),
    Column("last_observed_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))

scanner_heartbeats = Table("scanner_heartbeats", metadata,
    Column("scanner_scope", String(64), primary_key=True),
    Column("enabled", Boolean, nullable=False), Column("running", Boolean, nullable=False),
    Column("owner_id", String(64)), Column("lease_expires_at", DateTime(timezone=True)),
    Column("last_scan_started_at", DateTime(timezone=True)),
    Column("last_scan_completed_at", DateTime(timezone=True)),
    Column("last_successful_scan_at", DateTime(timezone=True)),
    Column("last_symbol_count", Integer, nullable=False), Column("last_error_count", Integer, nullable=False),
    Column("last_transition_count", Integer, nullable=False), Column("last_data_status", String(32)),
    Column("last_error_code", String(128)), Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False))

notifications = Table("notifications", metadata,
    Column("id", String(36), primary_key=True), Column("signal_id", String(36), ForeignKey("signals.id")),
    Column("channel", String(32), nullable=False), Column("destination_scope", String(64), nullable=False),
    Column("event_type", String(32), nullable=False), Column("event_fingerprint", String(255), nullable=False),
    Column("status", String(16), nullable=False), Column("attempt_count", Integer, nullable=False),
    Column("claimed_at", DateTime(timezone=True)), Column("claim_expires_at", DateTime(timezone=True)),
    Column("first_attempt_at", DateTime(timezone=True)), Column("last_attempt_at", DateTime(timezone=True)),
    Column("delivered_at", DateTime(timezone=True)), Column("error_code", String(128)),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("channel", "destination_scope", "event_fingerprint", name="uq_notification_event"))
Index("ix_notifications_status_claim", notifications.c.status, notifications.c.claim_expires_at)

notification_settings = Table("notification_settings", metadata,
    Column("destination_scope", String(64), primary_key=True), Column("radar", Boolean, nullable=False),
    Column("ready", Boolean, nullable=False), Column("invalidated", Boolean, nullable=False),
    Column("too_late", Boolean, nullable=False), Column("allow_b_quality", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))
