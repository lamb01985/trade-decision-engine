"""Conservative, append-only Phase 4C outcome assessment.

The evaluator is downstream of immutable research observations.  It never
calls or modifies the production detector and never infers intraminute order.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.historical_replay import (REPLAY_CACHE_VERSION, REPLAY_CONTRACT_VERSION,
    SESSION_CALENDAR_VERSION, regular_session_close)
from app.engines.market import ET
from app.models import Bar
from app.persistence import OutcomeAssessment
from app.persistence.contracts import ResearchLedger

UTC = timezone.utc
OUTCOME_CONTRACT_VERSION = "outcome-v1"
CALCULATION_VERSION = "minute-boundary-r-v1"
MFE_MAE_POLICY_VERSION = "ready-through-terminal-r-v1"
EVALUATION_HORIZON_ID = "intraday-rth-calendar-v1"
EVIDENCE_GRANULARITY = "MASSIVE_ADJUSTED_MINUTE_AGGREGATES"


@dataclass(frozen=True)
class OutcomeEvaluationResult:
    status: str
    assessment: OutcomeAssessment | None = None
    reason: str | None = None


@dataclass(frozen=True)
class OutcomeBatchResult:
    ready_eligible: int
    target_first: int
    invalidation_first: int
    neither: int
    ambiguous: int
    not_evaluable: int
    inserted: int
    duplicates: int
    missing_provenance: int


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def first_ready_observations(ledger: ResearchLedger) -> list[Mapping[str, Any]]:
    """Return exactly the first immutable READY observation for each instance."""
    ready: list[Mapping[str, Any]] = []
    for instance in ledger.instances():
        rows = ledger.observations(instance["setup_instance_id"])
        row = next((item for item in rows if item["event_type"] == "CANDIDATE"
                    and item["recommended_action"] == "READY"), None)
        if row is not None:
            ready.append(row)
    return ready


def _crosses(bar: Bar, direction: str, boundary: float, favorable: bool) -> bool:
    if direction == "LONG":
        return bar.high >= boundary if favorable else bar.low <= boundary
    return bar.low <= boundary if favorable else bar.high >= boundary


def _r(price: float, entry: float, risk: float, direction: str) -> float:
    return ((price - entry) if direction == "LONG" else (entry - price)) / risk


def _manifest_hash(items: Sequence[Mapping[str, Any]]) -> str:
    encoded = json.dumps(list(items), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def evaluate_ready_observation(observation: Mapping[str, Any], bars: Sequence[Bar],
                               *, computed_at: datetime | None = None) -> OutcomeEvaluationResult:
    """Assess one frozen READY observation using conservative minute evidence."""
    if observation.get("recommended_action") != "READY" or observation.get("event_type") != "CANDIDATE":
        return OutcomeEvaluationResult("NOT_EVALUABLE", reason="NOT_AN_IMMUTABLE_READY_OBSERVATION")
    direction = observation.get("direction")
    entry = observation.get("trigger")
    reference = observation.get("decision_reference_price")
    invalidation = observation.get("invalidation")
    target_1 = observation.get("target_1")
    target_2 = observation.get("target_2")
    if direction not in {"LONG", "SHORT"} or any(value is None for value in
            (entry, reference, invalidation, target_1)):
        return OutcomeEvaluationResult("NOT_EVALUABLE", reason="INCOMPLETE_FROZEN_READY_PLAN")
    risk = abs(float(entry) - float(invalidation))
    if risk <= 0:
        return OutcomeEvaluationResult("NOT_EVALUABLE", reason="NON_POSITIVE_FROZEN_INITIAL_RISK")

    start = _aware(observation["decision_bar_timestamp"])
    session_day = start.astimezone(ET).date()
    cutoff = datetime.combine(session_day, regular_session_close(session_day), tzinfo=ET).astimezone(UTC)
    if start >= cutoff:
        return OutcomeEvaluationResult("NOT_EVALUABLE", reason="READY_AT_OR_AFTER_SESSION_HORIZON")

    eligible = sorted((_aware(bar.timestamp), bar) for bar in bars
        if start < _aware(bar.timestamp) < cutoff
        and _aware(bar.timestamp).astimezone(ET).date() == session_day)
    timestamps = [item[0] for item in eligible]
    duplicates = sorted({value for value in timestamps if timestamps.count(value) > 1})
    unique = {timestamp: bar for timestamp, bar in eligible}
    expected: list[datetime] = []
    cursor = start + timedelta(minutes=1)
    while cursor < cutoff:
        expected.append(cursor)
        cursor += timedelta(minutes=1)
    missing = [value for value in expected if value not in unique]
    ordered = [(timestamp, unique[timestamp]) for timestamp in expected if timestamp in unique]

    target_at = invalidation_at = None
    target_bar = invalidation_bar = None
    ambiguity_reason = None
    first_observed_event_at = None
    for timestamp, bar in ordered:
        hit_target = _crosses(bar, direction, float(target_1), True)
        hit_invalidation = _crosses(bar, direction, float(invalidation), False)
        if hit_target and hit_invalidation:
            target_at = invalidation_at = timestamp
            target_bar = invalidation_bar = bar
            ambiguity_reason = "TARGET_AND_INVALIDATION_TOUCHED_IN_SAME_MINUTE"
            first_observed_event_at = timestamp
            break
        if hit_target:
            target_at, target_bar, first_observed_event_at = timestamp, bar, timestamp
            break
        if hit_invalidation:
            invalidation_at, invalidation_bar, first_observed_event_at = timestamp, bar, timestamp
            break

    gaps_before_event = [value for value in missing
                         if first_observed_event_at is None or value < first_observed_event_at]
    if duplicates:
        ambiguity_reason = ambiguity_reason or "DUPLICATE_MINUTE_EVIDENCE"
    if gaps_before_event:
        ambiguity_reason = ambiguity_reason or "MISSING_MINUTE_EVIDENCE_COULD_CHANGE_ORDERING"
    if ambiguity_reason:
        primary = "AMBIGUOUS"
    elif target_at is not None:
        primary = "TARGET_FIRST"
    elif invalidation_at is not None:
        primary = "INVALIDATION_FIRST"
    else:
        primary = "NEITHER"

    metric_rows: list[tuple[datetime, Bar]] = []
    for timestamp, bar in ordered:
        metric_rows.append((timestamp, bar))
        final_target = target_2 is not None and _crosses(bar, direction, float(target_2), True)
        if _crosses(bar, direction, float(invalidation), False) or final_target:
            break
    favorable = [max(0.0, _r(bar.high if direction == "LONG" else bar.low,
                              float(entry), risk, direction)) for _, bar in metric_rows]
    adverse = [max(0.0, -_r(bar.low if direction == "LONG" else bar.high,
                             float(entry), risk, direction)) for _, bar in metric_rows]
    last_at = metric_rows[-1][0] if metric_rows else start
    last_bar = metric_rows[-1][1] if metric_rows else None
    complete = not missing and not duplicates
    data_quality = dict(observation.get("data_quality") or {})
    provenance = {
        "source_mode": data_quality.get("source_mode"),
        "replay_run_id": data_quality.get("replay_run_id"),
        "replay_contract_version": REPLAY_CONTRACT_VERSION,
        "session_calendar_version": SESSION_CALENDAR_VERSION,
        "replay_cache_version": REPLAY_CACHE_VERSION,
        "outcome_contract_version": OUTCOME_CONTRACT_VERSION,
        "calculation_version": CALCULATION_VERSION,
        "mfe_mae_policy_version": MFE_MAE_POLICY_VERSION,
    }
    evidence_rows = [{"timestamp": timestamp.isoformat(), "open": bar.open, "high": bar.high,
                      "low": bar.low, "close": bar.close, "volume": bar.volume}
                     for timestamp, bar in ordered]
    evidence = {
        "provenance": provenance,
        "coverage": {"complete": complete, "expected_minutes": len(expected),
            "observed_minutes": len(unique), "missing_minutes": [value.isoformat() for value in missing],
            "duplicate_minutes": [value.isoformat() for value in duplicates]},
        "evidence_manifest_hash": _manifest_hash(evidence_rows),
        "first_target_bar": ({"timestamp": target_at.isoformat(), "high": target_bar.high,
                              "low": target_bar.low} if target_at and target_bar else None),
        "first_invalidation_bar": ({"timestamp": invalidation_at.isoformat(),
                                    "high": invalidation_bar.high, "low": invalidation_bar.low}
                                   if invalidation_at and invalidation_bar else None),
    }
    assessment_key = (f"{observation['observation_id']}:{OUTCOME_CONTRACT_VERSION}:"
                      f"{EVALUATION_HORIZON_ID}:{cutoff.isoformat()}")
    assessment = OutcomeAssessment(
        outcome_assessment_id=str(uuid.uuid5(uuid.NAMESPACE_URL, assessment_key)),
        setup_instance_id=observation["setup_instance_id"],
        reference_observation_id=observation["observation_id"], primary_outcome=primary,
        outcome_contract_version=OUTCOME_CONTRACT_VERSION,
        calculation_version=CALCULATION_VERSION,
        evaluation_horizon_id=EVALUATION_HORIZON_ID,
        evidence_granularity=EVIDENCE_GRANULARITY, evidence_start_at=start,
        evidence_cutoff_at=cutoff, computed_at=_aware(computed_at or cutoff),
        ready_reference_price=float(reference), theoretical_entry_price=float(entry),
        entry_reference_type="ENGINE_TRIGGER", initial_invalidation=float(invalidation),
        initial_risk_1r=risk, target_1_price=float(target_1),
        target_2_price=float(target_2) if target_2 is not None else None,
        target_1_r=_r(float(target_1), float(entry), risk, direction),
        target_2_r=_r(float(target_2), float(entry), risk, direction) if target_2 is not None else None,
        mfe_r=max(favorable, default=0.0), mae_r=max(adverse, default=0.0),
        terminal_r=(_r(last_bar.close, float(entry), risk, direction) if last_bar else
                    _r(float(reference), float(entry), risk, direction)),
        end_of_session_r=((_r(last_bar.close, float(entry), risk, direction) if last_bar else
                           _r(float(reference), float(entry), risk, direction))
                          if complete and (not expected or last_at == expected[-1]) else None),
        target_hit_at=target_at, invalidation_hit_at=invalidation_at,
        time_to_target_seconds=int((target_at - start).total_seconds()) if target_at else None,
        time_to_invalidation_seconds=int((invalidation_at - start).total_seconds()) if invalidation_at else None,
        terminal_reason=("AMBIGUOUS_EVIDENCE" if primary == "AMBIGUOUS" else
                         "TARGET_1" if primary == "TARGET_FIRST" else
                         "INVALIDATION" if primary == "INVALIDATION_FIRST" else "SESSION_ENDED"),
        ambiguity_reason=ambiguity_reason,
        expiration_outcome="SESSION_CLOSE" if primary == "NEITHER" else None,
        last_evidence_at=last_at, evidence=evidence)
    return OutcomeEvaluationResult("EVALUATED", assessment)


def append_evaluation(ledger: ResearchLedger, observation: Mapping[str, Any],
                      bars: Sequence[Bar]) -> OutcomeEvaluationResult:
    result = evaluate_ready_observation(observation, bars)
    if result.assessment is not None:
        ledger.append_outcome(result.assessment)
    return result


def evaluate_ledger_loaded(ledger: ResearchLedger,
                           bars_by_symbol: Mapping[str, Sequence[Bar]]) -> OutcomeBatchResult:
    """Evaluate all first-READY rows from already completeness-validated bars."""
    counts = {name: 0 for name in ("TARGET_FIRST", "INVALIDATION_FIRST", "NEITHER", "AMBIGUOUS")}
    inserted = duplicates = not_evaluable = missing_provenance = 0
    ready = first_ready_observations(ledger)
    symbols = {item["setup_instance_id"]: item["symbol"] for item in ledger.instances()}
    for observation in ready:
        result = evaluate_ready_observation(observation,
            bars_by_symbol.get(symbols.get(observation["setup_instance_id"], ""), ()))
        if result.assessment is None:
            not_evaluable += 1
            continue
        provenance = (result.assessment.evidence or {}).get("provenance", {})
        if not provenance.get("source_mode") or (
                provenance.get("source_mode") == "REPLAY" and not provenance.get("replay_run_id")):
            missing_provenance += 1
        counts[result.assessment.primary_outcome] += 1
        if ledger.append_outcome(result.assessment):
            inserted += 1
        else:
            duplicates += 1
    return OutcomeBatchResult(len(ready), counts["TARGET_FIRST"],
        counts["INVALIDATION_FIRST"], counts["NEITHER"], counts["AMBIGUOUS"],
        not_evaluable, inserted, duplicates, missing_provenance)
