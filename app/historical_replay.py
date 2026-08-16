"""Point-in-time historical replay into an isolated research ledger.

This module owns chronology and provenance only. Setup detection, scoring,
ranking, lifecycle, triggers, invalidations, and targets remain in the shared
production pipeline.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Mapping

from app.backtesting import HistoricalBarCache
from app.config import settings
from app.decision_intelligence import build_decision_intelligence
from app.engines.market import ET
from app.engines.pipeline import ProductionEvaluation, evaluate_production
from app.market_data.base import MarketDataError, MarketDataProvider
from app.market_data.massive import MassiveMarketDataProvider
from app.models import Bar, StandardDecision, SymbolAnalysis
from app.parameters import DEFAULT_PARAMETERS, EngineParameters
from app.persistence import ScanCycleCoverage, ScanSymbolCoverage, create_stores
from app.persistence.contracts import ResearchLedger
from app.research_identity import (COVERAGE_CONTRACT_VERSION,
    MEASUREMENT_CONTRACT_VERSION, OCCURRENCE_POLICY_VERSION, canonical_json)
from app.versioning import ENGINE_VERSION, PARAMETER_VERSION, SETUP_RULES_VERSION

UTC = timezone.utc
REPLAY_CONTRACT_VERSION = "historical-replay-v2"
BAR_VISIBILITY_VERSION = "completed-aggregate-v1"
SESSION_CALENDAR_VERSION = "us-equities-session-calendar-v1"
REPLAY_CACHE_VERSION = "massive-paginated-v1"


@dataclass(frozen=True)
class ReplayConfig:
    symbols: tuple[str, ...]
    start_date: date
    end_date: date
    output_database: Path
    cache_directory: Path = Path("data/replay_cache")
    session_type: str = "REGULAR"
    strict_session_schedule: bool = True
    parameters: EngineParameters = DEFAULT_PARAMETERS

    def __post_init__(self) -> None:
        symbols = tuple(dict.fromkeys(value.strip().upper() for value in self.symbols if value.strip()))
        if not symbols:
            raise ValueError("Replay requires at least one symbol")
        if self.start_date > self.end_date:
            raise ValueError("Replay start_date must not follow end_date")
        if self.session_type != "REGULAR":
            raise ValueError("historical-replay-v1 supports REGULAR evaluation only")
        if self.output_database.suffix not in {".sqlite", ".sqlite3", ".db"}:
            raise ValueError("Replay output must be an isolated SQLite database")
        object.__setattr__(self, "symbols", symbols)


@dataclass(frozen=True)
class ReplayResult:
    replay_run_id: str
    source_mode: str
    status: str
    symbols: tuple[str, ...]
    requested_start: date
    requested_end: date
    sessions_replayed: int
    cycles_processed: int
    symbol_evaluations: int
    source_bars: int
    setup_instances: int
    observations: int
    lifecycle_transitions: int
    qualifying_coverage: int
    no_setup_coverage: int
    not_observed_coverage: int
    gaps: int
    provider_requests: int
    cache_hits: int
    duration_seconds: float
    cycles_per_second: float
    bars_per_second: float
    engine_version: str = ENGINE_VERSION
    setup_rules_version: str = SETUP_RULES_VERSION
    parameter_version: str = PARAMETER_VERSION
    replay_contract_version: str = REPLAY_CONTRACT_VERSION
    occurrence_policy_version: str = OCCURRENCE_POLICY_VERSION
    measurement_contract_version: str = MEASUREMENT_CONTRACT_VERSION
    coverage_contract_version: str = COVERAGE_CONTRACT_VERSION
    bar_visibility_version: str = BAR_VISIBILITY_VERSION
    session_calendar_version: str = SESSION_CALENDAR_VERSION
    provider: str = "MASSIVE_ADJUSTED_MINUTE_AGGREGATES"


def _run_id(config: ReplayConfig, bars_by_symbol: Mapping[str, list[Bar]]) -> str:
    data_manifest = {
        symbol: [{"t": bar.timestamp, "o": bar.open, "h": bar.high, "l": bar.low,
                  "c": bar.close, "v": bar.volume} for bar in bars]
        for symbol, bars in sorted(bars_by_symbol.items())
    }
    payload = {"contract": REPLAY_CONTRACT_VERSION, "symbols": config.symbols,
        "start": config.start_date, "end": config.end_date,
        "session_type": config.session_type,
        "strict_session_schedule": config.strict_session_schedule,
        "session_calendar_version": SESSION_CALENDAR_VERSION,
        "parameters": config.parameters.as_dict(),
        "versions": [ENGINE_VERSION, SETUP_RULES_VERSION, PARAMETER_VERSION],
        "data": data_manifest}
    return "rpl_" + hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _is_regular_checkpoint(bar: Bar) -> bool:
    local = bar.timestamp.astimezone(ET)
    return time(9, 30) <= local.time() < time(16, 0) and local.minute % 5 == 4


def regular_session_close(day: date) -> time:
    """Return the versioned U.S. equity close, including recurring 13:00 sessions."""
    november_first = date(day.year, 11, 1)
    thanksgiving = november_first + timedelta(
        days=(3 - november_first.weekday()) % 7 + 21)
    black_friday = thanksgiving + timedelta(days=1)
    july_fourth = date(day.year, 7, 4)
    observed_independence = (july_fourth - timedelta(days=1)
        if july_fourth.weekday() == 5 else july_fourth + timedelta(days=1)
        if july_fourth.weekday() == 6 else july_fourth)
    independence_early = observed_independence - timedelta(days=1)
    while independence_early.weekday() >= 5:
        independence_early -= timedelta(days=1)
    christmas_eve = date(day.year, 12, 24)
    if day in {black_friday, independence_early} or (
            day == christmas_eve and day.weekday() < 5):
        return time(13, 0)
    return time(16, 0)


def _analysis(evaluation: ProductionEvaluation, cutoff: datetime) -> SymbolAnalysis:
    instrument = evaluation.instrument
    decision = evaluation.decision
    observed_at = cutoff + timedelta(minutes=1)
    return SymbolAnalysis(instrument=instrument, levels=evaluation.levels,
        setup=evaluation.legacy_setup, setup_score=evaluation.score,
        trade_plan=evaluation.legacy_plan, what_would_change_state=[],
        thesis="Historical point-in-time production evaluation.",
        current_structure="Derived only from the visible completed-bar prefix.",
        confirmation="Unchanged production setup semantics.", permission_to_trade=False,
        decision=decision, decision_intelligence=build_decision_intelligence(decision,
            evaluation.levels, data_state="LIVE_CURRENT", market_session="REGULAR"),
        provider_timestamp=cutoff, analysis_timestamp=observed_at,
        decision_bar_timestamp=cutoff, data_age_seconds=60, data_status="LIVE",
        data_state="LIVE_CURRENT", market_session="REGULAR",
        current_price=instrument.timeframes["1m"].price,
        price_source="MASSIVE_COMPLETED_MINUTE_AGGREGATE")


def replay_loaded(config: ReplayConfig, bars_by_symbol: Mapping[str, list[Bar]],
                  ledger: ResearchLedger) -> ReplayResult:
    """Replay preloaded data. No evaluation receives a bar after its cutoff."""
    started = monotonic()
    ordered = {symbol: sorted(bars_by_symbol.get(symbol, ()), key=lambda item: item.timestamp)
               for symbol in config.symbols}
    run_id = _run_id(config, ordered)
    observed_checkpoints = {bar.timestamp for bars in ordered.values() for bar in bars
        if config.start_date <= bar.timestamp.astimezone(ET).date() <= config.end_date
        and _is_regular_checkpoint(bar)}
    # Materialize the versioned session schedule. Otherwise a provider hole,
    # including a missing tail, would disappear from the denominator entirely.
    checkpoints: list[datetime] = []
    checkpoint_days = sorted({value.astimezone(ET).date() for value in observed_checkpoints})
    for day in checkpoint_days:
        daily = sorted(value for value in observed_checkpoints if value.astimezone(ET).date() == day)
        if not daily:
            continue
        current = (datetime.combine(day, time(9, 34), tzinfo=ET).astimezone(UTC)
                   if config.strict_session_schedule else daily[0])
        final = ((datetime.combine(day, regular_session_close(day), tzinfo=ET)
                  - timedelta(minutes=1)).astimezone(UTC)
                 if config.strict_session_schedule else daily[-1])
        while current <= final:
            checkpoints.append(current)
            current += timedelta(minutes=5)
    previous: dict[str, StandardDecision | None] = {symbol: None for symbol in config.symbols}
    previous_session: dict[str, date | None] = {symbol: None for symbol in config.symbols}
    coverage_counts = {"QUALIFYING_CANDIDATES": 0, "NO_SETUP": 0, "NOT_OBSERVED": 0}
    evaluations = gaps = 0
    sessions: set[date] = set()

    for cutoff in checkpoints:
        session_day = cutoff.astimezone(ET).date()
        sessions.add(session_day)
        cycle_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{cutoff.isoformat()}"))
        rows: list[ScanSymbolCoverage] = []
        for symbol in config.symbols:
            symbol_bars = ordered[symbol]
            exact = any(bar.timestamp == cutoff for bar in symbol_bars)
            if not exact:
                gaps += 1
                coverage_counts["NOT_OBSERVED"] += 1
                ledger.record_data_unavailable(symbol, observed_at=cutoff + timedelta(minutes=1),
                    session_date=session_day, data_state="DATA_UNAVAILABLE", cycle_id=cycle_id)
                rows.append(ScanSymbolCoverage(symbol, "DATA_UNAVAILABLE", "NOT_OBSERVED",
                    None, None, None, None, "UNAVAILABLE", "REGULAR",
                    error_code="MISSING_CHECKPOINT_BAR"))
                continue
            visible = [bar for bar in symbol_bars if bar.timestamp <= cutoff]
            assert all(bar.timestamp <= cutoff for bar in visible)
            if previous_session[symbol] != session_day:
                previous[symbol] = None
                previous_session[symbol] = session_day
            try:
                evaluation = evaluate_production(symbol, visible, previous[symbol], config.parameters)
            except (ValueError, IndexError, ZeroDivisionError):
                gaps += 1
                coverage_counts["NOT_OBSERVED"] += 1
                ledger.record_data_unavailable(symbol, observed_at=cutoff + timedelta(minutes=1),
                    session_date=session_day, data_state="DATA_UNAVAILABLE", cycle_id=cycle_id)
                rows.append(ScanSymbolCoverage(symbol, "DATA_UNAVAILABLE", "NOT_OBSERVED",
                    cutoff, cutoff, cutoff, 60, "UNAVAILABLE", "REGULAR",
                    error_code="INSUFFICIENT_VISIBLE_HISTORY"))
                continue
            previous[symbol] = evaluation.decision
            current = _analysis(evaluation, cutoff)
            instance_ids = ledger.observe_candidates(current, evaluation.candidates,
                config.parameters, cycle_id=cycle_id, source_mode="REPLAY", replay_run_id=run_id)
            ledger.reconcile_symbol(symbol, session_date=session_day,
                decision_bar_timestamp=cutoff, observed_instance_ids=instance_ids)
            classification = "QUALIFYING_CANDIDATES" if instance_ids else "NO_SETUP"
            coverage_counts[classification] += 1
            evaluations += 1
            rows.append(ScanSymbolCoverage(symbol, "SUCCESS", classification, cutoff, cutoff,
                cutoff, 60, "LIVE", "REGULAR", tuple(instance_ids)))
        failed = sum(row.attempt_status != "SUCCESS" for row in rows)
        ledger.record_scan_cycle(ScanCycleCoverage(cycle_id=cycle_id,
            scanner_scope="historical-replay", run_id=run_id,
            started_at=cutoff + timedelta(minutes=1), completed_at=cutoff + timedelta(minutes=1),
            coverage_status="COMPLETE" if not failed else "PARTIAL",
            market_session="REGULAR", data_status="HISTORICAL_COMPLETE" if not failed else "PARTIAL",
            expected_symbols=config.symbols, scanner_ready_at_start=True,
            scanner_ready_at_end=not failed, symbols=tuple(rows),
            error_code="HISTORICAL_DATA_GAP" if failed else None))

    for session_day in sessions:
        close_at = datetime.combine(
            session_day, regular_session_close(session_day), tzinfo=ET).astimezone(UTC)
        ledger.close_session(session_day, market_timestamp=close_at)
    instances = ledger.instances()
    observations = [row for instance in instances
                    for row in ledger.observations(instance["setup_instance_id"])]
    elapsed = monotonic() - started
    source_bars = sum(len(value) for value in ordered.values())
    return ReplayResult(run_id, "REPLAY", "COMPLETE" if not gaps else "PARTIAL",
        config.symbols, config.start_date, config.end_date, len(sessions), len(checkpoints),
        evaluations, source_bars, len(instances), len(observations),
        sum(row["event_type"] != "CANDIDATE" for row in observations),
        coverage_counts["QUALIFYING_CANDIDATES"], coverage_counts["NO_SETUP"],
        coverage_counts["NOT_OBSERVED"], gaps, 0, 0, elapsed,
        len(checkpoints) / elapsed if elapsed else 0.0,
        source_bars / elapsed if elapsed else 0.0)


async def run_replay(provider: MarketDataProvider, config: ReplayConfig) -> ReplayResult:
    """Fetch/cache historical input, then write only to the configured local ledger."""
    # Never trust legacy range-cache files that predate pagination/truncation
    # detection. The versioned subdirectory is the completeness provenance.
    cache = HistoricalBarCache(config.cache_directory / REPLAY_CACHE_VERSION)
    bars_by_symbol: dict[str, list[Bar]] = {}
    requests = hits = 0
    fetch_start = config.start_date - timedelta(days=10)
    for symbol in config.symbols:
        if cache.load(symbol, fetch_start, config.end_date) is not None:
            hits += 1
        else:
            requests += 1
        bars_by_symbol[symbol] = await cache.get_or_fetch(provider, symbol, fetch_start, config.end_date)
    config.output_database.parent.mkdir(parents=True, exist_ok=True)
    stores = create_stores(f"sqlite:///{config.output_database.resolve()}", initialize=True)
    try:
        result = replay_loaded(config, bars_by_symbol, stores.research)
        return ReplayResult(**{**asdict(result), "provider_requests": requests, "cache_hits": hits})
    finally:
        stores.signal.close()


async def _main(args: argparse.Namespace) -> None:
    if not settings.massive_api_key:
        raise SystemExit("MASSIVE_API_KEY is required")
    config = ReplayConfig(tuple(args.symbol), date.fromisoformat(args.start),
        date.fromisoformat(args.end), Path(args.output), Path(args.cache_dir))
    provider = MassiveMarketDataProvider(settings.massive_api_key, settings.massive_base_url)
    try:
        result = await run_replay(provider, config)
    finally:
        await provider.close()
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(asdict(result), default=str, indent=2) + "\n")
    print(json.dumps(asdict(result), default=str, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chronological research-ledger historical replay")
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", default="data/replay/research_replay.sqlite3")
    parser.add_argument("--cache-dir", default="data/replay_cache")
    parser.add_argument("--report", default="data/replay/replay_report.json")
    asyncio.run(_main(parser.parse_args()))
