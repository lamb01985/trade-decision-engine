"""Deterministic Phase 4D evidence analytics over immutable research facts."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.persistence.contracts import ResearchLedger

SCHEMA_VERSION = "evidence-artifact-v1"
ANALYTICS_CONTRACT_VERSION = "evidence-analytics-v1"
SLICE_CONTRACT_VERSION = "evidence-slices-v1"
SAMPLE_POLICY_VERSION = "evidence-sample-size-v1"
REALIZED_R_POLICY_VERSION = "phase4c-terminal-r-v1"

COHORT_KEYS = ("setup_family", "direction", "symbol", "evaluation_horizon_id",
    "setup_rules_version", "parameter_version", "outcome_contract_version", "source_mode")
SLICE_KEYS = ("time_of_day", "relative_volume", "volatility_atr", "trend_alignment",
              "market_regime", "symbol", "setup_family", "direction")
COMPATIBILITY_KEYS = ("identity_schema_version", "occurrence_policy_version",
    "measurement_contract_version", "engine_version", "setup_rules_version", "parameter_version",
    "observation_contract_version", "feature_definition_version", "outcome_contract_version",
    "calculation_version", "evaluation_horizon_id", "evidence_granularity", "source_mode",
    "replay_contract_version", "session_calendar_version", "replay_cache_version")


class EvidenceAnalyticsError(ValueError):
    pass


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise EvidenceAnalyticsError("Evidence artifacts cannot contain non-finite numbers")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _nested(mapping: Mapping[str, Any] | None, *keys: str) -> Any:
    value: Any = mapping or {}
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _bucket(value: Any, boundaries: Sequence[tuple[float, str]], final: str) -> str:
    number = _number(value)
    if number is None:
        return "UNAVAILABLE"
    for upper, label in boundaries:
        if number < upper:
            return label
    return final


def _slices(observation: Mapping[str, Any]) -> dict[str, str]:
    context = observation.get("market_context") or {}
    frames = _nested(context, "symbol_context", "timeframes") or {}
    frame_5m = frames.get("5m", {}) if isinstance(frames, Mapping) else {}
    frame_15m = frames.get("15m", {}) if isinstance(frames, Mapping) else {}
    minute = observation.get("minutes_since_regular_open")
    if not isinstance(minute, int) or minute < 0:
        time_segment = "UNKNOWN"
    elif minute < 60:
        time_segment = "OPENING"
    elif minute < 240:
        time_segment = "MIDDAY"
    else:
        time_segment = "LATE_SESSION"
    rvol = (frame_5m.get("relative_volume") if isinstance(frame_5m, Mapping) else None)
    atr_pct = _nested(context, "symbol_context", "atr_5m_pct")
    trend_5m = frame_5m.get("trend") if isinstance(frame_5m, Mapping) else None
    trend_15m = frame_15m.get("trend") if isinstance(frame_15m, Mapping) else None
    trend = (f"5M:{trend_5m}|15M:{trend_15m}"
             if trend_5m is not None and trend_15m is not None else "UNAVAILABLE")
    regime = _nested(context, "regime", "trend")
    if regime in (None, ""):
        regime = "UNAVAILABLE"
    return {
        "time_of_day": time_segment,
        "relative_volume": _bucket(rvol, ((0.8, "LOW"), (1.2, "NORMAL"),
                                                  (2.0, "ELEVATED")), "HIGH"),
        "volatility_atr": _bucket(atr_pct, ((0.5, "LOW"), (1.0, "NORMAL"),
                                                    (2.0, "ELEVATED")), "HIGH"),
        "trend_alignment": trend,
        "market_regime": str(regime),
        "symbol": str(observation.get("symbol", "UNKNOWN")),
        "setup_family": str(observation.get("setup_family", "UNKNOWN")),
        "direction": str(observation.get("direction", "UNKNOWN")),
    }


def _sample_label(n: int) -> str:
    if n < 20:
        return "INSUFFICIENT"
    if n < 50:
        return "PRELIMINARY"
    if n < 100:
        return "MODERATE_SAMPLE"
    return "SUBSTANTIAL_SAMPLE"


def _statistics(values: Sequence[float]) -> dict[str, Any]:
    n = len(values)
    wins = sum(value > 0 for value in values)
    losses = sum(value < 0 for value in values)
    neutral = n - wins - losses
    if not values:
        return {"win_count": 0, "loss_count": 0, "neutral_count": 0,
            "win_rate": None, "loss_rate": None, "positive_r_rate": None,
            "negative_r_rate": None, "average_realized_r": None,
            "median_realized_r": None, "cumulative_realized_r": None,
            "standard_deviation_realized_r": None, "minimum_realized_r": None,
            "maximum_realized_r": None, "standard_error_mean_r": None,
            "mean_r_95_confidence_interval": None}
    mean = statistics.fmean(values)
    standard_deviation = statistics.pstdev(values)
    standard_error = statistics.stdev(values) / math.sqrt(n) if n >= 2 else None
    confidence = ([mean - 1.96 * standard_error, mean + 1.96 * standard_error]
                  if standard_error is not None else None)
    return {"win_count": wins, "loss_count": losses, "neutral_count": neutral,
        "win_rate": wins / n, "loss_rate": losses / n,
        "positive_r_rate": wins / n, "negative_r_rate": losses / n,
        "average_realized_r": mean, "median_realized_r": statistics.median(values),
        "cumulative_realized_r": math.fsum(values),
        "standard_deviation_realized_r": standard_deviation,
        "minimum_realized_r": min(values), "maximum_realized_r": max(values),
        "standard_error_mean_r": standard_error,
        "mean_r_95_confidence_interval": confidence}


def _first_ready(ledger: ResearchLedger) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    ready: dict[str, Mapping[str, Any]] = {}
    errors: list[str] = []
    for instance in ledger.instances():
        instance_id = instance["setup_instance_id"]
        rows = [row for row in ledger.observations(instance_id)
                if row.get("event_type") == "CANDIDATE" and row.get("recommended_action") == "READY"]
        if rows:
            row = dict(rows[0])
            row.update({key: instance.get(key) for key in instance.keys() if key not in row})
            row["symbol"] = instance.get("symbol")
            ready[instance_id] = row
    return ready, errors


def _current_assessments(ledger: ResearchLedger, ready: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict], list[str]]:
    selected: list[dict] = []
    errors: list[str] = []
    for instance_id, observation in sorted(ready.items()):
        rows = [dict(row) for row in ledger.outcome_assessments(instance_id)]
        by_id = {row["outcome_assessment_id"]: row for row in rows}
        superseded: set[str] = set()
        for row in rows:
            prior = row.get("supersedes_assessment_id")
            if prior:
                if prior not in by_id:
                    errors.append(f"DANGLING_SUPERSESSION:{row['outcome_assessment_id']}")
                superseded.add(prior)
            visited: set[str] = set()
            cursor = row
            while cursor.get("supersedes_assessment_id") in by_id:
                cursor_id = cursor["outcome_assessment_id"]
                if cursor_id in visited:
                    errors.append(f"SUPERSESSION_CYCLE:{row['outcome_assessment_id']}")
                    break
                visited.add(cursor_id)
                cursor = by_id[cursor["supersedes_assessment_id"]]
        leaves = [row for row in rows if row["outcome_assessment_id"] not in superseded]
        for row in leaves:
            if row.get("reference_observation_id") != observation.get("observation_id"):
                errors.append(f"REFERENCE_MISMATCH:{row['outcome_assessment_id']}")
                continue
            evidence = row.get("evidence_json") or row.get("evidence") or {}
            provenance = evidence.get("provenance", {}) if isinstance(evidence, Mapping) else {}
            source_mode = provenance.get("source_mode")
            if source_mode not in {"LIVE", "REPLAY"}:
                errors.append(f"MISSING_SOURCE_MODE:{row['outcome_assessment_id']}")
                continue
            if source_mode == "REPLAY" and not provenance.get("replay_run_id"):
                errors.append(f"MISSING_REPLAY_RUN:{row['outcome_assessment_id']}")
                continue
            required_provenance = ("replay_contract_version", "session_calendar_version",
                                   "replay_cache_version")
            if any(not provenance.get(key) for key in required_provenance):
                errors.append(f"MISSING_CONTRACT_PROVENANCE:{row['outcome_assessment_id']}")
                continue
            item = {**observation, **row, "evidence": evidence, "provenance": provenance,
                    "source_mode": source_mode}
            item["slices"] = _slices(item)
            selected.append(item)
    return selected, errors


def _compatibility(item: Mapping[str, Any]) -> dict[str, Any]:
    provenance = item.get("provenance") or {}
    return {key: (provenance.get(key) if key in {"replay_contract_version",
        "session_calendar_version", "replay_cache_version"} else item.get(key))
        for key in COMPATIBILITY_KEYS}


def _dimension(item: Mapping[str, Any], key: str) -> Any:
    if key in SLICE_KEYS and key not in {"symbol", "setup_family", "direction"}:
        return item["slices"][key]
    return item.get(key, "UNKNOWN")


def build_evidence_artifact(ledger: ResearchLedger, *,
                            cohort_keys: Sequence[str] = ("setup_family",),
                            slice_keys: Sequence[str] = (),
                            filters: Mapping[str, str] | None = None,
                            reject_incompatible: bool = False) -> dict[str, Any]:
    """Build a semantic, content-addressed artifact without mutating the ledger."""
    cohort_keys = tuple(cohort_keys)
    slice_keys = tuple(slice_keys)
    unknown_cohort = set(cohort_keys) - set(COHORT_KEYS)
    unknown_slice = set(slice_keys) - set(SLICE_KEYS)
    if unknown_cohort or unknown_slice:
        raise EvidenceAnalyticsError(f"Unsupported dimensions: {sorted(unknown_cohort | unknown_slice)}")
    if len(set(cohort_keys + slice_keys)) != len(cohort_keys + slice_keys):
        raise EvidenceAnalyticsError("Cohort and slice dimensions must be unique")
    ready, errors = _first_ready(ledger)
    items, selection_errors = _current_assessments(ledger, ready)
    errors.extend(selection_errors)
    normalized_filters = dict(sorted((filters or {}).items()))
    for key in normalized_filters:
        if key not in set(COHORT_KEYS) | set(SLICE_KEYS):
            raise EvidenceAnalyticsError(f"Unsupported filter: {key}")
    items = [item for item in items if all(str(_dimension(item, key)) == str(value)
                                           for key, value in normalized_filters.items())]
    partitions: dict[str, list[dict]] = defaultdict(list)
    compatibility_values: dict[str, dict] = {}
    for item in items:
        compatible = _compatibility(item)
        digest = _hash(compatible)
        partitions[digest].append(item)
        compatibility_values[digest] = compatible
    if reject_incompatible and len(partitions) > 1:
        raise EvidenceAnalyticsError("Requested evidence spans incompatible contract versions")

    aggregates: list[dict[str, Any]] = []
    seen_memberships: set[tuple[str, str, tuple]] = set()
    for compatibility_id in sorted(partitions):
        groups: dict[tuple, list[dict]] = defaultdict(list)
        dimensions = cohort_keys + slice_keys
        for item in partitions[compatibility_id]:
            key = tuple(str(_dimension(item, name)) for name in dimensions)
            membership = (compatibility_id, str(item["setup_instance_id"]), key)
            if membership in seen_memberships:
                errors.append(f"DUPLICATE_INSTANCE_COHORT:{item['setup_instance_id']}")
            seen_memberships.add(membership)
            groups[key].append(item)
        if not groups and not dimensions:
            groups[()] = []
        for key in sorted(groups):
            group = groups[key]
            instance_ids = [str(item["setup_instance_id"]) for item in group]
            duplicate_ids = sorted({value for value in instance_ids if instance_ids.count(value) > 1})
            group_errors = list(errors)
            group_errors.extend(f"DUPLICATE_INSTANCE_COHORT:{value}" for value in duplicate_ids)
            ambiguous = [item for item in group if item.get("primary_outcome") == "AMBIGUOUS"]
            incomplete = [item for item in group if not bool(_nested(item.get("evidence"), "coverage", "complete"))]
            values: list[float] = []
            excluded = defaultdict(int)
            for item in group:
                if item.get("primary_outcome") == "AMBIGUOUS":
                    excluded["AMBIGUOUS"] += 1
                    continue
                if not bool(_nested(item.get("evidence"), "coverage", "complete")):
                    excluded["INCOMPLETE_EVIDENCE"] += 1
                    continue
                realized = _number(item.get("terminal_r"))
                if realized is None:
                    excluded["MISSING_ACCEPTED_TERMINAL_R"] += 1
                    continue
                values.append(realized)
            valid = not group_errors
            denominator = {"n_total": len(group), "n_evaluable": len(values),
                "n_incomplete": len(incomplete), "n_ambiguous": len(ambiguous),
                "excluded_reasons": dict(sorted(excluded.items()))}
            aggregate = {
                "compatibility_id": compatibility_id,
                "source_versions": compatibility_values[compatibility_id],
                "cohort": dict(zip(cohort_keys, key[:len(cohort_keys)])),
                "slice": dict(zip(slice_keys, key[len(cohort_keys):])),
                "valid": valid, "invalid_reasons": sorted(group_errors),
                "denominator": denominator,
                "statistics": _statistics(values) if valid else None,
                "sample_label": _sample_label(len(values)),
                "sample_instance_ids": sorted(instance_ids),
            }
            aggregates.append(aggregate)
    semantic_inputs = [{"setup_instance_id": item["setup_instance_id"],
        "assessment_is_current": True,
        "compatibility": _compatibility(item), "dimensions": {
            key: _dimension(item, key) for key in sorted(set(cohort_keys + slice_keys) | set(normalized_filters))},
        "primary_outcome": item.get("primary_outcome"),
        "terminal_r": item.get("terminal_r"),
        "coverage_complete": bool(_nested(item.get("evidence"), "coverage", "complete"))}
        for item in sorted(items, key=lambda row: (str(row["setup_instance_id"]),
                                                   str(row["outcome_assessment_id"])))]
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analytics_contract_version": ANALYTICS_CONTRACT_VERSION,
        "slice_contract_version": SLICE_CONTRACT_VERSION,
        "sample_policy_version": SAMPLE_POLICY_VERSION,
        "realized_r_policy_version": REALIZED_R_POLICY_VERSION,
        "cohort_definition": list(cohort_keys), "slice_definition": list(slice_keys),
        "filters": normalized_filters,
        "generation_input_fingerprint": _hash(semantic_inputs),
        "selection_errors": sorted(errors),
        "aggregates": sorted(aggregates, key=lambda row: canonical_json({
            "compatibility_id": row["compatibility_id"], "cohort": row["cohort"],
            "slice": row["slice"]})),
    }
    if errors and not artifact["aggregates"]:
        artifact["aggregates"] = [{"compatibility_id": "UNAVAILABLE",
            "source_versions": {}, "cohort": {}, "slice": {}, "valid": False,
            "invalid_reasons": sorted(errors), "denominator": {"n_total": len(ready),
                "n_evaluable": 0, "n_incomplete": 0, "n_ambiguous": 0,
                "excluded_reasons": {"DENOMINATOR_INTEGRITY_FAILURE": len(ready)}},
            "statistics": None, "sample_label": _sample_label(0),
            "sample_instance_ids": sorted(ready)}]
    artifact["artifact_fingerprint"] = _hash(artifact)
    return _plain(artifact)


def persist_artifact(artifact: Mapping[str, Any], directory: Path) -> Path:
    """Write one immutable content-addressed research artifact."""
    fingerprint = artifact.get("artifact_fingerprint")
    if not isinstance(fingerprint, str) or _hash({key: value for key, value in artifact.items()
                                                  if key != "artifact_fingerprint"}) != fingerprint:
        raise EvidenceAnalyticsError("Artifact fingerprint is invalid")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"phase4d-{fingerprint}.json"
    payload = canonical_json(artifact) + "\n"
    if path.exists():
        if path.read_text() != payload:
            raise EvidenceAnalyticsError("Content-addressed artifact path contains different bytes")
        return path
    path.write_text(payload)
    return path
