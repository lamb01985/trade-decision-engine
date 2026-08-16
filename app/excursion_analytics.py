"""Deterministic Phase 4E excursion and opportunity research analytics.

The builder is deliberately downstream of immutable READY observations.  It
accepts minute bars explicitly, performs no provider I/O, and never calls a
production decision path.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.engines.market import ET
from app.evidence_analytics import _sample_label, _slices
from app.historical_replay import (REPLAY_CACHE_VERSION, REPLAY_CONTRACT_VERSION,
                                   SESSION_CALENDAR_VERSION, regular_session_close)
from app.models import Bar
from app.persistence.contracts import ResearchLedger

UTC = timezone.utc
SCHEMA_VERSION = "excursion-artifact-v1"
MEASUREMENT_CONTRACT_VERSION = "excursion-measurement-v1"
PATH_CONTRACT_VERSION = "minute-ohlc-path-v1"
OPPORTUNITY_CONTRACT_VERSION = "ready-opportunity-context-v1"
AGGREGATION_CONTRACT_VERSION = "excursion-aggregation-v1"
HORIZONS = (5, 15, 30, 60, 120, "END_OF_SESSION")
THRESHOLDS_R = (0.25, 0.5, 1.0, 1.5, 2.0, 3.0)
COHORT_KEYS = ("setup_family", "direction", "symbol", "horizon_id", "outcome_state",
               "setup_rules_version", "parameter_version", "source_mode")
SLICE_KEYS = ("time_of_day", "relative_volume", "volatility_atr", "trend_alignment",
              "market_regime", "structural_space_atr", "symbol", "setup_family", "direction")
COMPATIBILITY_KEYS = ("identity_schema_version", "occurrence_policy_version",
    "measurement_contract_version", "engine_version", "setup_rules_version", "parameter_version",
    "observation_contract_version", "feature_definition_version", "context_contract_version",
    "regime_contract_version", "source_mode", "replay_contract_version",
    "session_calendar_version", "replay_cache_version", "outcome_contract_version",
    "outcome_calculation_version", "outcome_horizon_id")


class ExcursionAnalyticsError(ValueError):
    pass


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ExcursionAnalyticsError("Artifacts cannot contain non-finite numbers")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _stats(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, **{key: None for key in ("mean", "median", "standard_deviation", "minimum",
            "maximum", "p10", "p25", "p50", "p75", "p90")}}
    return {"n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
        "standard_deviation": statistics.pstdev(values), "minimum": min(values), "maximum": max(values),
        "p10": _percentile(values, .10), "p25": _percentile(values, .25),
        "p50": _percentile(values, .50), "p75": _percentile(values, .75),
        "p90": _percentile(values, .90)}


def _first_ready(ledger: ResearchLedger) -> list[dict[str, Any]]:
    selected = []
    for instance in ledger.instances():
        rows = sorted(ledger.observations(instance["setup_instance_id"]),
                      key=lambda row: (_aware(row["decision_bar_timestamp"]), str(row["observation_id"])))
        row = next((item for item in rows if item.get("event_type") == "CANDIDATE"
                    and item.get("recommended_action") == "READY"), None)
        if row is not None:
            selected.append({**instance, **row, "symbol": instance.get("symbol")})
    return sorted(selected, key=lambda row: str(row["setup_instance_id"]))


def _current_outcome(ledger: ResearchLedger, instance_id: str, observation_id: str) -> tuple[str, dict | None]:
    rows = [dict(row) for row in ledger.outcome_assessments(instance_id)]
    if not rows:
        return "UNAVAILABLE", None
    ids = {row["outcome_assessment_id"] for row in rows}
    superseded = {row.get("supersedes_assessment_id") for row in rows if row.get("supersedes_assessment_id")}
    if any(value not in ids for value in superseded):
        return "INCOMPATIBLE", None
    leaves = [row for row in rows if row["outcome_assessment_id"] not in superseded]
    leaves = [row for row in leaves if row.get("reference_observation_id") == observation_id]
    if len(leaves) != 1:
        return "INCOMPATIBLE", None
    return str(leaves[0].get("primary_outcome", "UNAVAILABLE")), leaves[0]


def _provenance(observation: Mapping[str, Any]) -> dict[str, Any]:
    quality = observation.get("data_quality") or {}
    return {"source_mode": quality.get("source_mode"), "replay_run_id": quality.get("replay_run_id"),
        "replay_contract_version": REPLAY_CONTRACT_VERSION,
        "session_calendar_version": SESSION_CALENDAR_VERSION,
        "replay_cache_version": REPLAY_CACHE_VERSION,
        "market_data_version": quality.get("market_data_version", REPLAY_CACHE_VERSION)}


def _opportunity(observation: Mapping[str, Any], reference: float | None) -> dict[str, Any]:
    context = observation.get("market_context") or {}
    symbol = context.get("symbol_context") or {}
    frame = (symbol.get("timeframes") or {}).get("5m") or {}
    direction = observation.get("direction")
    atr = _number(symbol.get("atr_5m"))
    vwap = _number(frame.get("vwap"))
    nearest = _number(observation.get("nearest_resistance" if direction == "LONG" else "nearest_support"))
    target = _number(observation.get("target_1"))

    def directional_distance(level: Any) -> float | None:
        level = _number(level)
        if reference is None or level is None:
            return None
        return (level - reference) if direction == "LONG" else (reference - level)

    space = directional_distance(nearest)
    raw = {"nearest_relevant_level": nearest, "distance_to_nearest_relevant_level": space,
        "distance_to_hod_lod": directional_distance(symbol.get("high_of_day" if direction == "LONG" else "low_of_day")),
        "distance_to_prior_day_high_low": directional_distance(symbol.get("prior_day_high" if direction == "LONG" else "prior_day_low")),
        "distance_to_opening_range": directional_distance(symbol.get("opening_range_high" if direction == "LONG" else "opening_range_low")),
        "distance_to_vwap": directional_distance(vwap), "atr_5m": atr,
        "structural_space_atr": (space / atr if space is not None and atr and atr > 0 else None),
        "structural_target_distance": directional_distance(target),
        "structural_risk_reward": _number(observation.get("initial_risk_reward")),
        "relative_volume": _number(frame.get("relative_volume")), "trend_5m": frame.get("trend"),
        "time_of_day_minutes": observation.get("minutes_since_regular_open"),
        "setup_family": observation.get("setup_family"), "direction": direction,
        "market_regime": (context.get("regime") or {}).get("trend", "UNAVAILABLE")}
    raw["availability"] = {key: ("AVAILABLE" if value is not None else "UNAVAILABLE")
                           for key, value in raw.items() if key != "availability"}
    return raw


def _space_bucket(value: Any) -> str:
    value = _number(value)
    if value is None:
        return "UNAVAILABLE"
    if value < .5:
        return "LT_0_5_ATR"
    if value < 1:
        return "0_5_TO_1_ATR"
    if value < 2:
        return "1_TO_2_ATR"
    return "GE_2_ATR"


def _cutoff(start: datetime, horizon: int | str) -> datetime:
    day = start.astimezone(ET).date()
    close = datetime.combine(day, regular_session_close(day), tzinfo=ET).astimezone(UTC)
    return close if horizon == "END_OF_SESSION" else min(start + timedelta(minutes=int(horizon)), close)


def _measure(observation: Mapping[str, Any], bars: Sequence[Bar], horizon: int | str,
             outcome_state: str) -> dict[str, Any]:
    start = _aware(observation["decision_bar_timestamp"])
    cutoff = _cutoff(start, horizon)
    reference = _number(observation.get("trigger"))
    invalidation = _number(observation.get("invalidation"))
    direction = observation.get("direction")
    risk = (abs(reference - invalidation) if reference is not None and invalidation is not None else None)
    risk_reason = None
    if risk is None:
        risk_reason = "MISSING_REFERENCE_OR_INVALIDATION"
    elif risk <= 0 or not math.isfinite(risk):
        risk, risk_reason = None, "NON_POSITIVE_OR_NON_FINITE_RISK"
    errors = []
    if reference is None:
        errors.append("MISSING_REFERENCE_PRICE")
    if direction not in {"LONG", "SHORT"}:
        errors.append("INVALID_DIRECTION")
    session_day = start.astimezone(ET).date()
    eligible = [(_aware(bar.timestamp), bar) for bar in bars if start < _aware(bar.timestamp) <= cutoff
                and _aware(bar.timestamp).astimezone(ET).date() == session_day]
    eligible.sort(key=lambda item: item[0])
    duplicates = sorted(stamp for stamp, count in Counter(stamp for stamp, _ in eligible).items()
                        if count > 1)
    unique = {stamp: bar for stamp, bar in eligible}
    expected = []
    cursor = start + timedelta(minutes=1)
    include_cutoff = cutoff.astimezone(ET).time() != regular_session_close(session_day)
    while cursor < cutoff or (include_cutoff and cursor == cutoff):
        expected.append(cursor)
        cursor += timedelta(minutes=1)
    missing = [stamp for stamp in expected if stamp not in unique]
    ordered = [(stamp, unique[stamp]) for stamp in expected if stamp in unique]
    session_close = datetime.combine(session_day, regular_session_close(session_day),
                                     tzinfo=ET).astimezone(UTC)
    session_truncated = (horizon != "END_OF_SESSION"
        and start + timedelta(minutes=int(horizon)) >= session_close)
    complete = not errors and not missing and not duplicates and not session_truncated
    incomplete_reasons = list(errors)
    if missing:
        incomplete_reasons.append("MISSING_FORWARD_BARS")
    if duplicates:
        incomplete_reasons.append("DUPLICATE_FORWARD_BARS")
    if session_truncated:
        incomplete_reasons.append("SESSION_BOUNDARY_TRUNCATION")
    if not ordered:
        incomplete_reasons.append("INSUFFICIENT_FORWARD_HISTORY")

    def favorable(bar: Bar) -> float:
        return max(0.0, (bar.high - reference) if direction == "LONG" else (reference - bar.low))

    def adverse(bar: Bar) -> float:
        return max(0.0, (reference - bar.low) if direction == "LONG" else (bar.high - reference))

    mfe = mae = None
    mfe_at = mae_at = None
    threshold_rows: dict[str, dict[str, Any]] = {}
    ambiguity: set[str] = set()
    invalidation_at = None
    if not errors and ordered:
        favorable_rows = [(favorable(bar), stamp) for stamp, bar in ordered]
        adverse_rows = [(adverse(bar), stamp) for stamp, bar in ordered]
        mfe, mfe_at = max(favorable_rows, key=lambda item: (item[0], -item[1].timestamp()))
        mae, mae_at = max(adverse_rows, key=lambda item: (item[0], -item[1].timestamp()))
        for stamp, bar in ordered:
            inv_hit = adverse(bar) >= risk if risk is not None else False
            if inv_hit and invalidation_at is None:
                invalidation_at = stamp
            for threshold in THRESHOLDS_R:
                key = f"{threshold:g}R"
                hit = risk is not None and favorable(bar) >= threshold * risk
                if key not in threshold_rows and hit:
                    same_bar_inv = inv_hit
                    threshold_rows[key] = {"reached": None if same_bar_inv else True,
                        "first_reached_at": None if same_bar_inv else stamp,
                        "time_to_seconds": None if same_bar_inv else int((stamp - start).total_seconds()),
                        "ambiguous": same_bar_inv,
                        "ambiguity_reason": "THRESHOLD_AND_INVALIDATION_SAME_MINUTE" if same_bar_inv else None}
                    if same_bar_inv:
                        ambiguity.add("THRESHOLD_AND_INVALIDATION_SAME_MINUTE")
        for threshold in THRESHOLDS_R:
            key = f"{threshold:g}R"
            threshold_rows.setdefault(key, {"reached": False if risk is not None else None,
                "first_reached_at": None,
                "time_to_seconds": None, "ambiguous": False, "ambiguity_reason": None})
    else:
        for threshold in THRESHOLDS_R:
            threshold_rows[f"{threshold:g}R"] = {"reached": None, "first_reached_at": None,
                "time_to_seconds": None, "ambiguous": False, "ambiguity_reason": None}

    before_inv = [(stamp, bar) for stamp, bar in ordered if invalidation_at is None or stamp < invalidation_at]
    mfe_before_inv = max((favorable(bar) for _, bar in before_inv), default=None) if not errors else None
    half = threshold_rows.get("0.5R", {})
    favorable_at = half.get("first_reached_at")
    before_favorable = [(stamp, bar) for stamp, bar in ordered if favorable_at is None or stamp < favorable_at]
    mae_before_favorable = max((adverse(bar) for _, bar in before_favorable), default=None) if not errors else None
    if favorable_at is not None:
        threshold_bar = next(bar for stamp, bar in ordered if stamp == favorable_at)
        if adverse(threshold_bar) > 0:
            ambiguity.add("MAE_AND_MEANINGFUL_FAVORABLE_SAME_MINUTE")
    end_return = None
    if not errors and ordered:
        end_return = ((ordered[-1][1].close - reference) if direction == "LONG"
                      else (reference - ordered[-1][1].close))
    for value in threshold_rows.values():
        reached_at = value["first_reached_at"]
        value["invalidation_before_threshold"] = (None if not complete or value["ambiguous"]
            or risk is None else bool(invalidation_at is not None
                and (reached_at is None or invalidation_at < reached_at)))
    end_return_payload = {"price": end_return,
        "percent": (end_return / reference * 100 if end_return is not None and reference else None),
        "r": (end_return / risk if end_return is not None and risk else None)}
    opportunity = _opportunity(observation, reference)
    slices = _slices(observation)
    slices["structural_space_atr"] = _space_bucket(opportunity["structural_space_atr"])
    return {"setup_instance_id": str(observation["setup_instance_id"]),
        "reference_observation_id": str(observation["observation_id"]),
        "reference_at": start, "reference_price": reference, "reference_price_type": "ENGINE_TRIGGER",
        "direction": direction, "setup_family": observation.get("setup_family"),
        "symbol": observation.get("symbol"), "horizon_id": (f"{horizon}M" if isinstance(horizon, int) else horizon),
        "horizon_cutoff_at": cutoff, "outcome_state": outcome_state,
        "risk_1r": risk, "risk_unavailable_reason": risk_reason,
        "coverage": {"complete": complete, "expected_minutes": len(expected),
            "observed_minutes": len(ordered), "missing_minutes": [x.isoformat() for x in missing],
            "duplicate_minutes": [x.isoformat() for x in duplicates],
            "session_boundary_truncated": session_truncated,
            "incomplete_reasons": sorted(set(incomplete_reasons))},
        "ambiguous": bool(ambiguity), "ambiguity_reasons": sorted(ambiguity),
        "mfe": {"price": mfe, "percent": (mfe / reference * 100 if mfe is not None and reference else None),
            "r": (mfe / risk if mfe is not None and risk else None), "at": mfe_at,
            "time_seconds": int((mfe_at - start).total_seconds()) if mfe_at else None},
        "mae": {"price": mae, "percent": (mae / reference * 100 if mae is not None and reference else None),
            "r": (mae / risk if mae is not None and risk else None), "at": mae_at,
            "time_seconds": int((mae_at - start).total_seconds()) if mae_at else None},
        "thresholds": threshold_rows, "invalidation_at": invalidation_at,
        "mfe_before_invalidation": {"price": mfe_before_inv,
            "r": (mfe_before_inv / risk if mfe_before_inv is not None and risk else None)},
        "mae_before_meaningful_favorable": {"threshold_r": .5, "price": mae_before_favorable,
            "r": (mae_before_favorable / risk if mae_before_favorable is not None and risk else None)},
        "end_return": end_return_payload,
        "end_of_session_return": end_return_payload if horizon == "END_OF_SESSION" else None,
        "opportunity": opportunity, "slices": slices}


def _compatibility(observation: Mapping[str, Any], provenance: Mapping[str, Any]) -> dict[str, Any]:
    return {key: (MEASUREMENT_CONTRACT_VERSION if key == "measurement_contract_version" else
        provenance.get(key) if key in provenance else observation.get(key)) for key in COMPATIBILITY_KEYS}


def _dimension(sample: Mapping[str, Any], key: str) -> str:
    if key in SLICE_KEYS and key not in {"symbol", "setup_family", "direction"}:
        return str(sample["slices"].get(key, "UNAVAILABLE"))
    return str(sample.get(key, "UNKNOWN"))


def _aggregate(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    # OHLC ordering ambiguity is metric-specific.  It does not erase directly
    # observable extrema; affected threshold/path fields carry their own null.
    evaluable = [row for row in samples if row["coverage"]["complete"]]
    metric = lambda path: [value for row in evaluable
        if (value := row[path[0]][path[1]]) is not None]
    threshold_rates = {}
    for threshold in THRESHOLDS_R:
        key = f"{threshold:g}R"
        eligible = [row["thresholds"][key] for row in evaluable
                    if row["thresholds"][key]["reached"] is not None]
        hits = [row for row in eligible if row["reached"]]
        threshold_rates[key] = {"n_evaluable": len(eligible), "n_reached": len(hits),
            "probability_reached": len(hits) / len(eligible) if eligible else None,
            "time_to_threshold_seconds": _stats([row["time_to_seconds"] for row in hits])}
    successful = [row for row in evaluable if row["thresholds"]["1R"]["reached"] is True]
    return {"denominator": {"n_total": len(samples), "n_evaluable": len(evaluable),
        "n_incomplete": sum(not row["coverage"]["complete"] for row in samples),
        "n_ambiguous": sum(row["ambiguous"] for row in samples)},
        "sample_label": _sample_label(len(evaluable)),
        "metrics": {"mfe_price": _stats(metric(("mfe", "price"))),
            "mae_price": _stats(metric(("mae", "price"))), "mfe_r": _stats(metric(("mfe", "r"))),
            "mae_r": _stats(metric(("mae", "r"))), "time_to_mfe_seconds": _stats(metric(("mfe", "time_seconds"))),
            "time_to_mae_seconds": _stats(metric(("mae", "time_seconds"))),
            "successful_mae_before_success_r": _stats([row["mae_before_meaningful_favorable"]["r"]
                for row in successful if row["mae_before_meaningful_favorable"]["r"] is not None
                and "MAE_AND_MEANINGFUL_FAVORABLE_SAME_MINUTE" not in row["ambiguity_reasons"]])},
        "thresholds": threshold_rates, "sample_instance_ids": sorted({row["setup_instance_id"] for row in samples})}


def build_excursion_artifact(ledger: ResearchLedger, bars_by_symbol: Mapping[str, Sequence[Bar]], *,
        horizons: Sequence[int | str] = HORIZONS, cohort_keys: Sequence[str] = ("setup_family",),
        slice_keys: Sequence[str] = (), filters: Mapping[str, str] | None = None,
        reject_incompatible: bool = False) -> dict[str, Any]:
    """Build immutable semantic measurements and aggregates without ledger mutation."""
    if set(cohort_keys) - set(COHORT_KEYS) or set(slice_keys) - set(SLICE_KEYS):
        raise ExcursionAnalyticsError("Unsupported cohort or slice dimension")
    if len(set(tuple(cohort_keys) + tuple(slice_keys))) != len(tuple(cohort_keys) + tuple(slice_keys)):
        raise ExcursionAnalyticsError("Cohort and slice dimensions must be unique")
    normalized_horizons = tuple(horizons)
    for horizon in normalized_horizons:
        if horizon != "END_OF_SESSION" and (not isinstance(horizon, int) or horizon <= 0):
            raise ExcursionAnalyticsError(f"Invalid horizon: {horizon}")
    filters = dict(sorted((filters or {}).items()))
    if set(filters) - (set(COHORT_KEYS) | set(SLICE_KEYS)):
        raise ExcursionAnalyticsError("Unsupported filter")
    samples = []
    for observation in _first_ready(ledger):
        provenance = _provenance(observation)
        if provenance["source_mode"] not in {"LIVE", "REPLAY"} or (
                provenance["source_mode"] == "REPLAY" and not provenance["replay_run_id"]):
            continue
        outcome, outcome_row = _current_outcome(ledger, str(observation["setup_instance_id"]),
                                                str(observation["observation_id"]))
        for horizon in normalized_horizons:
            row = _measure(observation, bars_by_symbol.get(str(observation["symbol"]), ()), horizon, outcome)
            # A horizon is a measurement-semantic boundary even when callers do
            # not request it as a visible cohort dimension.
            outcome_versions = {"outcome_contract_version": (outcome_row or {}).get("outcome_contract_version"),
                "outcome_calculation_version": (outcome_row or {}).get("calculation_version"),
                "outcome_horizon_id": (outcome_row or {}).get("evaluation_horizon_id")}
            compatibility = {**_compatibility(observation, {**provenance, **outcome_versions}),
                             "horizon_id": row["horizon_id"]}
            compatibility_id = _hash(compatibility)
            row.update({"compatibility": compatibility, "compatibility_id": compatibility_id,
                        "provenance": provenance, "engine_version": observation.get("engine_version"),
                        "source_mode": provenance.get("source_mode"),
                        "setup_rules_version": observation.get("setup_rules_version"),
                        "parameter_version": observation.get("parameter_version"),
                        "observation_contract_version": observation.get("observation_contract_version")})
            if all(_dimension(row, key) == str(value) for key, value in filters.items()):
                samples.append(row)
    partitions = {row["compatibility_id"] for row in samples}
    if reject_incompatible and len(partitions) > 1:
        raise ExcursionAnalyticsError("Requested evidence spans incompatible contract versions")
    groups = defaultdict(list)
    dimensions = tuple(cohort_keys) + tuple(slice_keys)
    for row in samples:
        groups[(row["compatibility_id"], tuple(_dimension(row, key) for key in dimensions))].append(row)
    aggregates = []
    for (compatibility_id, values), rows in sorted(groups.items()):
        aggregate = _aggregate(rows)
        aggregate.update({"compatibility_id": compatibility_id,
            "source_versions": rows[0]["compatibility"],
            "cohort": dict(zip(cohort_keys, values[:len(cohort_keys)])),
            "slice": dict(zip(slice_keys, values[len(cohort_keys):]))})
        aggregates.append(aggregate)
    semantic_samples = sorted(samples, key=lambda row: (row["setup_instance_id"], row["horizon_id"]))
    artifact = {"schema_version": SCHEMA_VERSION,
        "measurement_contract_version": MEASUREMENT_CONTRACT_VERSION,
        "path_contract_version": PATH_CONTRACT_VERSION,
        "opportunity_contract_version": OPPORTUNITY_CONTRACT_VERSION,
        "aggregation_contract_version": AGGREGATION_CONTRACT_VERSION,
        "horizon_definition": [f"{x}M" if isinstance(x, int) else x for x in normalized_horizons],
        "threshold_definition_r": list(THRESHOLDS_R), "cohort_definition": list(cohort_keys),
        "slice_definition": list(slice_keys), "filters": filters,
        "generation_input_fingerprint": _hash(semantic_samples),
        "measurements": semantic_samples, "aggregates": aggregates}
    artifact["artifact_fingerprint"] = _hash(artifact)
    return _plain(artifact)


def persist_artifact(artifact: Mapping[str, Any], directory: Path) -> Path:
    fingerprint = artifact.get("artifact_fingerprint")
    semantic = {key: value for key, value in artifact.items() if key != "artifact_fingerprint"}
    if not isinstance(fingerprint, str) or _hash(semantic) != fingerprint:
        raise ExcursionAnalyticsError("Artifact fingerprint is invalid")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"phase4e-{fingerprint}.json"
    payload = canonical_json(artifact) + "\n"
    if path.exists() and path.read_text() != payload:
        raise ExcursionAnalyticsError("Content-addressed artifact path contains different bytes")
    if not path.exists():
        path.write_text(payload)
    return path
