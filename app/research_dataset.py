"""Phase 4F deterministic, resumable historical research dataset orchestration.

This research-only layer composes the accepted replay, outcome, and excursion
pipelines.  It has no import or call path to notifications, brokerage, or
production persistence.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Mapping, Sequence

from app.config import settings
from app.backtesting import HistoricalBarCache
from app.evidence_analytics import (ANALYTICS_CONTRACT_VERSION,
    REALIZED_R_POLICY_VERSION, build_evidence_artifact)
from app.excursion_analytics import (AGGREGATION_CONTRACT_VERSION,
    MEASUREMENT_CONTRACT_VERSION as EXCURSION_CONTRACT_VERSION,
    OPPORTUNITY_CONTRACT_VERSION, PATH_CONTRACT_VERSION,
    build_excursion_artifact)
from app.engines.market import ET
from app.historical_replay import (BAR_VISIBILITY_VERSION, REPLAY_CACHE_VERSION,
    REPLAY_CONTRACT_VERSION, SESSION_CALENDAR_VERSION, ReplayConfig, replay_loaded)
from app.models import Bar
from app.market_data.massive import MassiveMarketDataProvider
from app.outcome_evaluator import (CALCULATION_VERSION, OUTCOME_CONTRACT_VERSION,
    evaluate_ledger_loaded)
from app.parameters import DEFAULT_PARAMETERS
from app.persistence import create_stores
from app.research_identity import (COVERAGE_CONTRACT_VERSION,
    FEATURE_DEFINITION_VERSION, MEASUREMENT_CONTRACT_VERSION,
    OBSERVATION_CONTRACT_VERSION, OCCURRENCE_POLICY_VERSION, canonical_json)
from app.versioning import ENGINE_VERSION, PARAMETER_VERSION, SETUP_RULES_VERSION

UTC = timezone.utc
DATASET_CONTRACT_VERSION = "phase4f-research-dataset-v1"
MANIFEST_CONTRACT_VERSION = "phase4f-run-manifest-v1"
ORCHESTRATION_VERSION = "phase4f-symbol-session-v1"
PARTITION_CONTRACT_VERSION = "chronological-60-20-20-v1"
SOURCE_MODE = "REPLAY"
PROVIDER_VERSION = "MASSIVE_ADJUSTED_MINUTE_AGGREGATES"
DEFAULT_RESEARCH_UNIVERSE = (
    "QQQ", "SPY", "IWM", "SMH", "NVDA", "AMD", "AMZN", "META", "AAPL",
    "MSFT", "AVGO", "TSLA", "GOOGL")


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def compatibility(source_commit_sha: str) -> dict[str, str]:
    return {
        "source_commit_sha": source_commit_sha,
        "engine_version": ENGINE_VERSION,
        "setup_rules_version": SETUP_RULES_VERSION,
        "parameter_version": PARAMETER_VERSION,
        "occurrence_identity_version": OCCURRENCE_POLICY_VERSION,
        "observation_version": OBSERVATION_CONTRACT_VERSION,
        "feature_version": FEATURE_DEFINITION_VERSION,
        "measurement_version": MEASUREMENT_CONTRACT_VERSION,
        "coverage_version": COVERAGE_CONTRACT_VERSION,
        "outcome_contract_version": OUTCOME_CONTRACT_VERSION,
        "outcome_calculation_version": CALCULATION_VERSION,
        "evidence_version": ANALYTICS_CONTRACT_VERSION,
        "realized_r_policy_version": REALIZED_R_POLICY_VERSION,
        "excursion_contract_version": EXCURSION_CONTRACT_VERSION,
        "excursion_path_version": PATH_CONTRACT_VERSION,
        "opportunity_contract_version": OPPORTUNITY_CONTRACT_VERSION,
        "excursion_aggregation_version": AGGREGATION_CONTRACT_VERSION,
        "source_mode": SOURCE_MODE,
        "replay_version": REPLAY_CONTRACT_VERSION,
        "bar_visibility_version": BAR_VISIBILITY_VERSION,
        "market_data_provider_version": PROVIDER_VERSION,
        "market_data_cache_version": REPLAY_CACHE_VERSION,
        "exchange_calendar_version": SESSION_CALENDAR_VERSION,
    }


@dataclass(frozen=True)
class ResearchRunConfig:
    symbols: tuple[str, ...]
    sessions: tuple[date, ...]
    output_directory: Path
    source_commit_sha: str
    max_concurrency: int = 1

    def __post_init__(self) -> None:
        symbols = tuple(dict.fromkeys(x.strip().upper() for x in self.symbols if x.strip()))
        sessions = tuple(sorted(set(self.sessions)))
        if not symbols or not sessions:
            raise ValueError("Research run requires symbols and sessions")
        if self.max_concurrency < 1 or self.max_concurrency > 8:
            raise ValueError("max_concurrency must be between 1 and 8")
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "sessions", sessions)

    @property
    def versions(self) -> dict[str, str]:
        return compatibility(self.source_commit_sha)

    @property
    def research_run_id(self) -> str:
        semantic = {"contract": MANIFEST_CONTRACT_VERSION, "symbols": self.symbols,
            "sessions": self.sessions, "versions": self.versions,
            "orchestration": ORCHESTRATION_VERSION}
        return "rsr_" + _hash(semantic)


@dataclass
class UnitResult:
    symbol: str
    session: date
    status: str
    failure_class: str | None = None
    retryable: bool = False
    exclusion_reason: str | None = None
    coverage: dict[str, int] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    artifact_fingerprints: dict[str, str] = field(default_factory=dict)
    performance: dict[str, float] = field(default_factory=dict)


class CompatibilityError(RuntimeError):
    pass


def chronological_partitions(sessions: Sequence[date]) -> dict[str, Any]:
    ordered = sorted(set(sessions))
    if len(ordered) < 5:
        return {"contract_version": PARTITION_CONTRACT_VERSION, "status": "INSUFFICIENT_HISTORY",
            "reason": "AT_LEAST_FIVE_SESSIONS_REQUIRED", "assignments": {}}
    development_end = max(1, int(len(ordered) * .6))
    validation_end = max(development_end + 1, int(len(ordered) * .8))
    validation_end = min(validation_end, len(ordered) - 1)
    assignments = {}
    for index, session in enumerate(ordered):
        assignments[session.isoformat()] = ("DEVELOPMENT" if index < development_end else
            "VALIDATION" if index < validation_end else "HOLDOUT")
    return {"contract_version": PARTITION_CONTRACT_VERSION, "status": "READY",
        "selection_basis": "CHRONOLOGY_ONLY_NOT_PERFORMANCE", "assignments": assignments,
        "holdout_policy": "UNTOUCHED_UNTIL_EXPLICIT_FUTURE_FINAL_EVALUATION_CONTRACT"}


def _unit_key(config: ResearchRunConfig, symbol: str, session: date) -> str:
    return _hash({"symbol": symbol, "session": session, "versions": config.versions,
                  "orchestration": ORCHESTRATION_VERSION})


def _unit_path(config: ResearchRunConfig, symbol: str, session: date) -> Path:
    return config.output_directory / "units" / f"{session.isoformat()}-{symbol}-{_unit_key(config, symbol, session)[:12]}.json"


def _write_immutable(path: Path, value: Any) -> None:
    payload = canonical_json(_plain(value)) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() != payload:
        raise CompatibilityError(f"immutable artifact collision: {path.name}")
    if not path.exists():
        path.write_text(payload)


def _load_completed(config: ResearchRunConfig, symbol: str, session: date) -> UnitResult | None:
    path = _unit_path(config, symbol, session)
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if value.get("unit_key") != _unit_key(config, symbol, session):
        raise CompatibilityError("completed unit has incompatible fingerprint")
    if value.get("status") != "COMPLETE":
        return None
    fields = {key: value[key] for key in UnitResult.__dataclass_fields__}
    fields["session"] = date.fromisoformat(fields["session"])
    fields["performance"] = {key: float(item) for key, item in fields["performance"].items()}
    return UnitResult(**fields)


def _dataset_record(measurement: Mapping[str, Any], outcome: Mapping[str, Any] | None,
                    config: ResearchRunConfig, replay_run_id: str) -> dict[str, Any]:
    row = dict(measurement)
    # Accepted ledger row IDs and SQL created_at are storage metadata (UUID4 and
    # wall clock). Replace them with content identities so independent replays
    # have identical dataset semantics without changing the source ledger.
    stable_observation_id = "obs_" + _hash({"setup_instance_id": row["setup_instance_id"],
        "reference_at": row["reference_at"], "reference_price": row["reference_price"],
        "setup_family": row["setup_family"], "direction": row["direction"]})
    row["reference_observation_id"] = stable_observation_id
    if outcome:
        normalized_outcome = dict(outcome)
        normalized_outcome.pop("created_at", None)
        normalized_outcome["reference_observation_id"] = stable_observation_id
        normalized_outcome["outcome_assessment_id"] = "out_" + _hash({
            "setup_instance_id": row["setup_instance_id"],
            "reference_observation_id": stable_observation_id,
            "outcome_contract_version": normalized_outcome.get("outcome_contract_version"),
            "calculation_version": normalized_outcome.get("calculation_version"),
            "evaluation_horizon_id": normalized_outcome.get("evaluation_horizon_id")})
        row["outcome"] = _plain(normalized_outcome)
    else:
        row["outcome"] = None
    row["provenance"] = {**row.get("provenance", {}), **config.versions,
        "research_run_id": config.research_run_id, "replay_run_id": replay_run_id}
    row["partition"] = chronological_partitions(config.sessions)["assignments"].get(
        str(row["reference_at"])[:10])
    return _plain(row)


def process_loaded_unit(config: ResearchRunConfig, symbol: str, session: date,
                        bars: Sequence[Bar]) -> UnitResult:
    started = monotonic()
    unit_dir = config.output_directory / "working" / _unit_key(config, symbol, session)
    database = unit_dir / "ledger.sqlite3"
    if unit_dir.exists():
        shutil.rmtree(unit_dir)
    unit_dir.mkdir(parents=True)
    stores = create_stores(f"sqlite:///{database.resolve()}", initialize=True)
    try:
        replay_started = monotonic()
        replay = replay_loaded(ReplayConfig((symbol,), session, session, database,
            config.output_directory / "cache"), {symbol: list(bars)}, stores.research)
        replay_seconds = monotonic() - replay_started
        outcome_started = monotonic()
        outcome = evaluate_ledger_loaded(stores.research, {symbol: bars})
        outcome_seconds = monotonic() - outcome_started
        evidence_started = monotonic()
        evidence = build_evidence_artifact(stores.research,
            cohort_keys=("setup_family", "direction", "symbol"),
            slice_keys=("time_of_day", "relative_volume", "volatility_atr",
                        "trend_alignment", "market_regime"))
        evidence_seconds = monotonic() - evidence_started
        excursion_started = monotonic()
        artifact = build_excursion_artifact(stores.research, {symbol: bars},
            horizons=(5, 15, 30, 60, 120, "END_OF_SESSION"),
            cohort_keys=("setup_family", "direction", "symbol"),
            slice_keys=("time_of_day", "relative_volume", "volatility_atr",
                        "trend_alignment", "market_regime", "structural_space_atr"))
        excursion_seconds = monotonic() - excursion_started
        outcomes = {}
        for instance in stores.research.instances():
            rows = stores.research.outcome_assessments(instance["setup_instance_id"])
            if rows:
                outcomes[str(instance["setup_instance_id"])] = dict(rows[-1])
        records = [_dataset_record(row, outcomes.get(row["setup_instance_id"]), config,
                                   replay.replay_run_id)
                   for row in artifact["measurements"] if row["horizon_id"] == "END_OF_SESSION"]
        status = "COMPLETE" if replay.status == "COMPLETE" else "INCOMPLETE"
        coverage = {"cycles": replay.cycles_processed, "setup_instances": replay.setup_instances,
            "ready_instances": outcome.ready_eligible, "monitor_observations": sum(
                row["recommended_action"] == "MONITOR" for instance in stores.research.instances()
                for row in stores.research.observations(instance["setup_instance_id"])),
            "no_setup_cycles": replay.no_setup_coverage,
            "not_observed_cycles": replay.not_observed_coverage,
            "data_gaps": replay.gaps, "outcome_assessments": outcome.inserted,
            "evaluable_outcomes": outcome.target_first + outcome.invalidation_first + outcome.neither,
            "ambiguous_outcomes": outcome.ambiguous, "incomplete_outcomes": outcome.not_evaluable,
            "excursion_assessments": len(records),
            "evaluable_excursions": sum(row["coverage"]["complete"] for row in records),
            "incomplete_excursions": sum(not row["coverage"]["complete"] for row in records),
            "unavailable_structural_fields": sum(sum(v == "UNAVAILABLE" for v in
                row["opportunity"]["availability"].values()) for row in records)}
        return UnitResult(symbol, session, status,
            failure_class=None if status == "COMPLETE" else "PROVIDER_OR_REPLAY_INCOMPLETE",
            retryable=False, exclusion_reason=None if status == "COMPLETE" else "EXPLICIT_DATA_GAPS",
            coverage=coverage, records=records,
            artifact_fingerprints={"replay": replay.replay_run_id,
                "evidence": evidence["artifact_fingerprint"],
                "excursion_semantic": _hash(records)},
            performance={"replay_seconds": replay_seconds, "outcome_seconds": outcome_seconds,
                "evidence_seconds": evidence_seconds, "excursion_seconds": excursion_seconds,
                "end_to_end_seconds": monotonic() - started})
    finally:
        stores.signal.close()
        shutil.rmtree(unit_dir, ignore_errors=True)


def run_loaded(config: ResearchRunConfig, bars_by_unit: Mapping[tuple[str, date], Sequence[Bar]],
               processor: Callable[[ResearchRunConfig, str, date, Sequence[Bar]], UnitResult] = process_loaded_unit,
               *, stop_after: int | None = None) -> dict[str, Any]:
    """Run deterministic granular units. COMPLETE compatible units are reused."""
    config.output_directory.mkdir(parents=True, exist_ok=True)
    results: list[UnitResult] = []
    run_started = monotonic()
    processed = 0
    for symbol in config.symbols:
        for session in config.sessions:
            prior = _load_completed(config, symbol, session)
            if prior is not None:
                results.append(prior)
                continue
            if stop_after is not None and processed >= stop_after:
                return build_outputs(config, results, state="INTERRUPTED",
                                     elapsed_seconds=monotonic() - run_started)
            bars = bars_by_unit.get((symbol, session))
            if bars is None:
                result = UnitResult(symbol, session, "UNAVAILABLE",
                    failure_class="PROVIDER_PERMANENT_UNAVAILABLE_DATA", retryable=False,
                    exclusion_reason="NO_VERSIONED_BARS", coverage={"provider_unavailable": 1})
            else:
                try:
                    result = processor(config, symbol, session, bars)
                except CompatibilityError:
                    raise
                except Exception as exc:
                    result = UnitResult(symbol, session, "FAILED",
                        failure_class=type(exc).__name__, retryable=False,
                        exclusion_reason="DETERMINISTIC_PIPELINE_FAILURE")
            processed += 1
            if result.status == "COMPLETE":
                payload = {**_plain(asdict(result)), "unit_key": _unit_key(config, symbol, session),
                    "versions": config.versions}
                _write_immutable(_unit_path(config, symbol, session), payload)
            results.append(result)
            build_outputs(config, results, state="RUNNING", elapsed_seconds=monotonic() - run_started)
    return build_outputs(config, results, state="COMPLETE", elapsed_seconds=monotonic() - run_started)


async def run_provider(config: ResearchRunConfig) -> dict[str, Any]:
    """Fetch accepted paginated Massive data into its versioned cache, then run."""
    from datetime import timedelta
    if not settings.massive_api_key:
        raise RuntimeError("MASSIVE_API_KEY is required for a provider research run")
    provider = MassiveMarketDataProvider(settings.massive_api_key, settings.massive_base_url)
    cache = HistoricalBarCache(config.output_directory / "cache" / REPLAY_CACHE_VERSION)
    bars_by_unit: dict[tuple[str, date], list[Bar]] = defaultdict(list)
    try:
        for symbol in config.symbols:
            bars = await cache.get_or_fetch(provider, symbol,
                config.sessions[0] - timedelta(days=10), config.sessions[-1])
            requested = set(config.sessions)
            for bar in bars:
                session = bar.timestamp.astimezone(ET).date()
                if session in requested:
                    bars_by_unit[(symbol, session)].append(bar)
    finally:
        await provider.close()
    return run_loaded(config, bars_by_unit)


def _coverage(results: Sequence[UnitResult]) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    for result in results:
        totals.update(result.coverage)
    return {**dict(sorted(totals.items())),
        "sessions_requested": len(results),
        "sessions_completed": sum(x.status == "COMPLETE" for x in results),
        "sessions_unavailable": sum(x.status == "UNAVAILABLE" for x in results),
        "sessions_incomplete": sum(x.status == "INCOMPLETE" for x in results),
        "sessions_failed": sum(x.status == "FAILED" for x in results)}


def _baseline(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def counts(key: str) -> dict[str, int]:
        return dict(sorted(Counter(str(row.get(key, "UNAVAILABLE")) for row in records).items()))
    outcomes = Counter(str((row.get("outcome") or {}).get("primary_outcome", "INCOMPLETE"))
                       for row in records)
    distributions = lambda path: sorted(float(value) for row in records
        if (value := path(row)) is not None)
    return {"scope": "DESCRIPTIVE_ONLY_NO_OPTIMIZATION", "sample_size": len(records),
        "counts_by_family": counts("setup_family"), "counts_by_direction": counts("direction"),
        "counts_by_symbol": counts("symbol"),
        "counts_by_time_of_day": dict(sorted(Counter(str(row.get("slices", {}).get("time_of_day", "UNAVAILABLE"))
            for row in records).items())), "outcome_counts": dict(sorted(outcomes.items())),
        "terminal_r": distributions(lambda x: (x.get("outcome") or {}).get("terminal_r")),
        "mfe": distributions(lambda x: x.get("mfe", {}).get("price")),
        "mae": distributions(lambda x: x.get("mae", {}).get("price")),
        "mfe_r": distributions(lambda x: x.get("mfe", {}).get("r")),
        "mae_r": distributions(lambda x: x.get("mae", {}).get("r")),
        "time_to_mfe_seconds": distributions(lambda x: x.get("mfe", {}).get("time_seconds")),
        "time_to_mae_seconds": distributions(lambda x: x.get("mae", {}).get("time_seconds")),
        "structural_room_atr": distributions(lambda x: x.get("opportunity", {}).get("structural_space_atr")),
        "threshold_hit_counts": {key: dict(Counter(str(row.get("thresholds", {}).get(key, {}).get("reached"))
            for row in records)) for key in ("0.25R", "0.5R", "1R", "1.5R", "2R", "3R")}}


def build_outputs(config: ResearchRunConfig, results: Sequence[UnitResult], *, state: str,
                  elapsed_seconds: float = 0.0) -> dict[str, Any]:
    records = sorted((record for result in results for record in result.records),
                     key=lambda x: (x["setup_instance_id"], x["horizon_id"]))
    dataset_semantic = {"contract_version": DATASET_CONTRACT_VERSION,
        "research_run_id": config.research_run_id, "versions": config.versions,
        "partitions": chronological_partitions(config.sessions), "records": records}
    dataset_fingerprint = _hash(dataset_semantic)
    dataset = {**dataset_semantic, "semantic_fingerprint": dataset_fingerprint}
    coverage = _coverage(results)
    coverage["sessions_requested"] = len(config.symbols) * len(config.sessions)
    failures = [{"symbol": x.symbol, "session": x.session, "status": x.status,
        "failure_class": x.failure_class, "retryable": x.retryable,
        "exclusion_reason": x.exclusion_reason} for x in results if x.status != "COMPLETE"]
    performance = {key: sum(x.performance.get(key, 0.0) for x in results)
                   for key in ("replay_seconds", "outcome_seconds", "evidence_seconds",
                               "excursion_seconds", "end_to_end_seconds")}
    manifest_semantic = {"contract_version": MANIFEST_CONTRACT_VERSION,
        "research_run_id": config.research_run_id, "dataset_contract_version": DATASET_CONTRACT_VERSION,
        "source_commit_sha": config.source_commit_sha, "symbol_universe": list(config.symbols),
        "requested_date_range": [config.sessions[0], config.sessions[-1]],
        "requested_sessions": [x.isoformat() for x in config.sessions],
        "actual_session_coverage": sorted(f"{x.session}:{x.symbol}" for x in results if x.status == "COMPLETE"),
        "versions": config.versions, "source_mode": SOURCE_MODE,
        "total_sessions_requested": len(config.symbols) * len(config.sessions),
        "coverage": coverage, "failures_and_exclusions": failures,
        "artifact_fingerprints": {"dataset": dataset_fingerprint,
            "units": dict(sorted((f"{x.session}:{x.symbol}", x.artifact_fingerprints)
                                  for x in results if x.artifact_fingerprints))},
        "partitions": chronological_partitions(config.sessions)}
    manifest = {**_plain(manifest_semantic), "semantic_fingerprint": _hash(manifest_semantic)}
    baseline = _baseline(records)
    total_units = len(config.symbols) * len(config.sessions)
    completed_units = len(results)
    rate = completed_units / elapsed_seconds if elapsed_seconds else None
    status = {"research_run_id": config.research_run_id, "state": state,
        "symbols_completed": len({x.symbol for x in results if x.status == "COMPLETE"}),
        "symbols_total": len(config.symbols), "sessions_completed": coverage["sessions_completed"],
        "sessions_total": total_units,
        "current_symbol_session": (f"{results[-1].symbol}:{results[-1].session}" if results else None),
        "failures": coverage["sessions_failed"], "incomplete_units": coverage["sessions_incomplete"],
        "setup_instances": coverage.get("setup_instances", 0),
        "outcome_records": coverage.get("outcome_assessments", 0),
        "excursion_records": coverage.get("excursion_assessments", 0),
        "elapsed_seconds": elapsed_seconds,
        "estimated_remaining_seconds": ((total_units - completed_units) / rate if rate else None),
        "performance": performance}
    for name, value in (("dataset.json", dataset), ("manifest.json", manifest),
                        ("baseline.json", baseline), ("status.json", status)):
        path = config.output_directory / name
        path.write_text(canonical_json(_plain(value)) + "\n")
    return {"manifest": manifest, "dataset": dataset, "baseline": baseline, "status": status}


def _parse_sessions(start: date, end: date) -> tuple[date, ...]:
    from datetime import timedelta
    result, current = [], start
    while current <= end:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return tuple(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 4F research-only dataset status surface")
    parser.add_argument("command", choices=("status", "run"))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--symbols", default=",".join(DEFAULT_RESEARCH_UNIVERSE))
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--source-commit")
    args = parser.parse_args()
    if args.command == "status":
        path = args.run_dir / "status.json"
        if not path.exists():
            raise SystemExit("No Phase 4F status exists for this run directory")
        print(json.dumps(json.loads(path.read_text()), sort_keys=True, indent=2))
    else:
        if not all((args.start, args.end, args.source_commit)):
            raise SystemExit("run requires --start, --end, and --source-commit")
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        cfg = ResearchRunConfig(tuple(args.symbols.split(",")), _parse_sessions(start, end),
                                args.run_dir, args.source_commit)
        print(json.dumps(asyncio.run(run_provider(cfg))["status"], sort_keys=True, indent=2))
