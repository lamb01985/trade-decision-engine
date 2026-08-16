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

# Phase 4 research tables are additive and append-only at the repository API.
# They intentionally do not replace the mutable operational signal tables.
setup_instances = Table("setup_instances", metadata,
    Column("setup_instance_id", String(68), primary_key=True),
    Column("identity_schema_version", String(32), nullable=False),
    Column("occurrence_policy_version", String(32), nullable=False),
    Column("measurement_contract_version", String(32), nullable=False),
    Column("symbol", String(16), nullable=False), Column("session_date", Date, nullable=False),
    Column("setup_family", String(64), nullable=False), Column("direction", String(8), nullable=False),
    Column("family_anchor_type", String(64), nullable=False), Column("anchor_key", String(64), nullable=False),
    Column("family_anchor_json", DOCUMENT, nullable=False),
    Column("episode_started_at", DateTime(timezone=True), nullable=False),
    Column("first_observed_at", DateTime(timezone=True), nullable=False),
    Column("source_signal_fingerprint", String(64)),
    Column("engine_version", String(32), nullable=False),
    Column("setup_rules_version", String(32), nullable=False),
    Column("parameter_version", String(32), nullable=False),
    Column("parameter_snapshot_hash", String(64), nullable=False),
    Column("setup_catalog_version", String(32), nullable=False),
    Column("research_status", String(16), nullable=False),
    Column("actionable_allowed", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("symbol", "session_date", "setup_family", "direction", "anchor_key",
                     "episode_started_at", "engine_version", "setup_rules_version", "parameter_version",
                     name="uq_setup_instance_identity"),
    CheckConstraint("direction IN ('LONG','SHORT','NEUTRAL')", name="ck_setup_instances_direction"),
    CheckConstraint("research_status IN ('PRODUCTION','EXPERIMENTAL','REJECTED')",
                    name="ck_setup_instances_research_status"))
Index("ix_setup_instances_symbol_started", setup_instances.c.symbol, setup_instances.c.episode_started_at)
Index("ix_setup_instances_session_family", setup_instances.c.session_date, setup_instances.c.setup_family)
Index("ix_setup_instances_versions", setup_instances.c.engine_version,
      setup_instances.c.setup_rules_version, setup_instances.c.parameter_version)
Index("ix_setup_instances_active_anchor", setup_instances.c.symbol, setup_instances.c.session_date,
      setup_instances.c.setup_family, setup_instances.c.direction, setup_instances.c.anchor_key)

setup_observations = Table("setup_observations", metadata,
    Column("observation_id", String(36), primary_key=True),
    Column("setup_instance_id", String(68), ForeignKey("setup_instances.setup_instance_id"), nullable=False),
    Column("cycle_id", String(36)), Column("observation_fingerprint", String(64), nullable=False, unique=True),
    Column("semantic_fingerprint", String(64), nullable=False),
    Column("event_type", String(32), nullable=False), Column("closure_reason", String(64)),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("market_timestamp", DateTime(timezone=True), nullable=False),
    Column("decision_bar_timestamp", DateTime(timezone=True), nullable=False),
    Column("feature_source_at", DateTime(timezone=True), nullable=False),
    Column("market_session", String(32), nullable=False),
    Column("minutes_since_regular_open", Integer), Column("day_of_week", Integer, nullable=False),
    Column("setup_family", String(64), nullable=False), Column("direction", String(8), nullable=False),
    Column("setup_state", String(32)), Column("recommended_action", String(32)),
    Column("quality", String(16)), Column("score", Integer), Column("rank_in_scan", Integer),
    Column("universe_size", Integer), Column("qualifying_candidate_count", Integer, nullable=False),
    Column("decision_reference_price", Float), Column("trigger", Float),
    Column("decision_zone_lower", Float), Column("decision_zone_upper", Float),
    Column("invalidation", Float), Column("target_1", Float), Column("target_2", Float),
    Column("initial_risk", Float), Column("initial_risk_reward", Float),
    Column("nearest_support", Float), Column("nearest_resistance", Float),
    Column("score_factors", DOCUMENT, nullable=False), Column("levels_snapshot", DOCUMENT, nullable=False),
    Column("confirmation_components", DOCUMENT, nullable=False),
    Column("missing_confirmation", DOCUMENT, nullable=False),
    Column("market_context", DOCUMENT, nullable=False), Column("data_quality", DOCUMENT, nullable=False),
    Column("feature_manifest", DOCUMENT, nullable=False), Column("parameter_snapshot", DOCUMENT, nullable=False),
    Column("engine_version", String(32), nullable=False),
    Column("setup_rules_version", String(32), nullable=False),
    Column("parameter_version", String(32), nullable=False),
    Column("parameter_snapshot_hash", String(64), nullable=False),
    Column("observation_contract_version", String(32), nullable=False),
    Column("feature_definition_version", String(32), nullable=False),
    Column("context_contract_version", String(32), nullable=False),
    Column("regime_contract_version", String(32), nullable=False),
    Column("closure_policy_version", String(32), nullable=False),
    Column("setup_catalog_version", String(32), nullable=False),
    Column("research_status", String(16), nullable=False),
    Column("actionable_allowed", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("feature_source_at <= decision_bar_timestamp", name="ck_observation_no_lookahead"),
    CheckConstraint("event_type IN ('CANDIDATE','ABSENCE_PENDING','CLOSURE','DATA_UNAVAILABLE')",
                    name="ck_observation_event_type"))
Index("ix_setup_observations_instance_time", setup_observations.c.setup_instance_id,
      setup_observations.c.market_timestamp)
Index("ix_setup_observations_family_state_time", setup_observations.c.setup_family,
      setup_observations.c.setup_state, setup_observations.c.market_timestamp)
Index("ix_setup_observations_action_time", setup_observations.c.recommended_action,
      setup_observations.c.market_timestamp)

setup_outcome_assessments = Table("setup_outcome_assessments", metadata,
    Column("outcome_assessment_id", String(36), primary_key=True),
    Column("setup_instance_id", String(68), ForeignKey("setup_instances.setup_instance_id"), nullable=False),
    Column("reference_observation_id", String(36), ForeignKey("setup_observations.observation_id")),
    Column("supersedes_assessment_id", String(36), ForeignKey("setup_outcome_assessments.outcome_assessment_id")),
    Column("primary_outcome", String(32), nullable=False), Column("terminal_reason", String(64)),
    Column("ambiguity_reason", String(255)), Column("expiration_outcome", String(64)),
    Column("outcome_contract_version", String(32), nullable=False),
    Column("calculation_version", String(32), nullable=False),
    Column("evaluation_horizon_id", String(64), nullable=False),
    Column("evidence_granularity", String(32), nullable=False),
    Column("evidence_start_at", DateTime(timezone=True), nullable=False),
    Column("evidence_cutoff_at", DateTime(timezone=True), nullable=False),
    Column("computed_at", DateTime(timezone=True), nullable=False),
    Column("ready_reference_price", Float), Column("theoretical_entry_price", Float),
    Column("entry_reference_type", String(32), nullable=False), Column("initial_invalidation", Float),
    Column("initial_risk_1r", Float), Column("target_1_price", Float), Column("target_2_price", Float),
    Column("target_1_r", Float), Column("target_2_r", Float), Column("mfe_r", Float), Column("mae_r", Float),
    Column("terminal_r", Float), Column("end_of_session_r", Float),
    Column("target_hit_at", DateTime(timezone=True)), Column("invalidation_hit_at", DateTime(timezone=True)),
    Column("time_to_target_seconds", Integer), Column("time_to_invalidation_seconds", Integer),
    Column("last_evidence_at", DateTime(timezone=True)), Column("evidence_json", DOCUMENT, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("reference_observation_id", "outcome_contract_version",
                     "evaluation_horizon_id", "evidence_cutoff_at", name="uq_outcome_assessment"),
    CheckConstraint("primary_outcome IN ('TARGET_FIRST','INVALIDATION_FIRST','NEITHER','AMBIGUOUS')",
                    name="ck_outcome_primary"),
    CheckConstraint("evidence_cutoff_at >= evidence_start_at", name="ck_outcome_evidence_window"))
Index("ix_outcomes_instance_computed", setup_outcome_assessments.c.setup_instance_id,
      setup_outcome_assessments.c.computed_at)

research_scan_cycles = Table("research_scan_cycles", metadata,
    Column("cycle_id", String(36), primary_key=True), Column("scanner_scope", String(64), nullable=False),
    Column("run_id", String(64), nullable=False), Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
    Column("coverage_status", String(32), nullable=False), Column("market_session", String(32), nullable=False),
    Column("data_status", String(32), nullable=False), Column("expected_universe_count", Integer, nullable=False),
    Column("universe_hash", String(64), nullable=False), Column("universe_version", String(32), nullable=False),
    Column("attempted_count", Integer, nullable=False), Column("succeeded_count", Integer, nullable=False),
    Column("failed_count", Integer, nullable=False), Column("not_attempted_count", Integer, nullable=False),
    Column("earliest_provider_timestamp", DateTime(timezone=True)),
    Column("latest_provider_timestamp", DateTime(timezone=True)), Column("maximum_data_age_seconds", Integer),
    Column("scanner_ready_at_start", Boolean, nullable=False), Column("scanner_ready_at_end", Boolean, nullable=False),
    Column("engine_version", String(32), nullable=False), Column("setup_rules_version", String(32), nullable=False),
    Column("parameter_version", String(32), nullable=False), Column("scanner_config_version", String(32), nullable=False),
    Column("occurrence_policy_version", String(32), nullable=False),
    Column("measurement_contract_version", String(32), nullable=False),
    Column("coverage_contract_version", String(32), nullable=False), Column("error_code", String(128)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("coverage_status IN ('COMPLETE','PARTIAL','FAILED','SKIPPED_OVERLAP','CANCELLED')",
                    name="ck_scan_cycle_coverage_status"))
Index("ix_research_scan_cycles_started", research_scan_cycles.c.started_at)

research_scan_symbol_coverage = Table("research_scan_symbol_coverage", metadata,
    Column("cycle_id", String(36), ForeignKey("research_scan_cycles.cycle_id"), primary_key=True),
    Column("symbol", String(16), primary_key=True), Column("attempt_status", String(32), nullable=False),
    Column("result_classification", String(32), nullable=False),
    Column("market_timestamp", DateTime(timezone=True)),
    Column("decision_bar_timestamp", DateTime(timezone=True)),
    Column("provider_timestamp", DateTime(timezone=True)), Column("data_age_seconds", Integer),
    Column("data_status", String(32), nullable=False), Column("market_session", String(32), nullable=False),
    Column("candidate_count", Integer, nullable=False), Column("candidate_instance_ids", DOCUMENT, nullable=False),
    Column("error_code", String(128)), Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("attempt_status IN ('SUCCESS','PROVIDER_ERROR','DATA_STALE','DATA_UNAVAILABLE','ENGINE_ERROR','NOT_ATTEMPTED','CANCELLED')",
                    name="ck_scan_symbol_attempt_status"),
    CheckConstraint("result_classification IN ('QUALIFYING_CANDIDATES','NO_SETUP','NOT_OBSERVED')",
                    name="ck_scan_symbol_result"))
Index("ix_scan_symbol_symbol_time", research_scan_symbol_coverage.c.symbol,
      research_scan_symbol_coverage.c.decision_bar_timestamp)
