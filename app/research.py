"""Chronological research replay using the same deterministic live setup engine."""
import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from statistics import mean, median

from app.config import settings
from app.engines.decision import build_symbol_trade_plan, evaluate_symbol_setup, score_setup
from app.engines.levels import build_levels
from app.engines.market import ET, analyze_symbol, regular_session_bars
from app.engines.setups import build_standard_decision
from app.market_data.massive import MassiveMarketDataProvider
from app.models import Bar, SetupProgress, StandardDecision
from app.parameters import DEFAULT_PARAMETERS, EngineParameters


@dataclass(frozen=True)
class ResearchPeriods:
    development_start: date
    development_end: date
    validation_start: date
    validation_end: date

    def period_for(self, timestamp: datetime) -> str | None:
        day = timestamp.date()
        if self.development_start <= day <= self.development_end: return "DEVELOPMENT"
        if self.validation_start <= day <= self.validation_end: return "VALIDATION"
        return None


@dataclass
class ResearchOccurrence:
    symbol: str
    timestamp: datetime
    period: str
    setup: str
    direction: str
    entry_trigger: float
    invalidation: float
    target_1: float
    target_2: float
    mfe: float
    mae: float
    outcome_r: float
    time_to_target_minutes: float | None
    time_to_invalidation_minutes: float | None


def measure_occurrence(decision: StandardDecision, future: list[Bar], period: str) -> ResearchOccurrence:
    risk = abs(decision.trigger-decision.invalidation)
    favorable = lambda b: b.high-decision.trigger if decision.direction == "LONG" else decision.trigger-b.low
    adverse = lambda b: decision.trigger-b.low if decision.direction == "LONG" else b.high-decision.trigger
    mfe = max((favorable(b) for b in future), default=0)
    mae = max((adverse(b) for b in future), default=0)
    target_hit = invalidation_hit = None
    outcome = 0.0
    for bar in future:
        hit_t2 = bar.high >= decision.target_2 if decision.direction == "LONG" else bar.low <= decision.target_2
        hit_t1 = bar.high >= decision.target_1 if decision.direction == "LONG" else bar.low <= decision.target_1
        hit_inv = bar.low <= decision.invalidation if decision.direction == "LONG" else bar.high >= decision.invalidation
        # Same-bar ordering is unknowable from OHLC; count it conservatively as invalidated.
        if hit_inv:
            invalidation_hit, outcome = bar.timestamp, -1.0
            break
        if hit_t2:
            target_hit, outcome = bar.timestamp, abs(decision.target_2-decision.trigger)/risk
            break
        if hit_t1:
            target_hit, outcome = bar.timestamp, abs(decision.target_1-decision.trigger)/risk
            break
    minutes = lambda ts: (ts-decision.timestamp).total_seconds()/60 if ts else None
    return ResearchOccurrence(decision.symbol, decision.timestamp, period, decision.setup_family.value,
        decision.direction, decision.trigger, decision.invalidation, decision.target_1, decision.target_2,
        mfe/risk, mae/risk, outcome, minutes(target_hit), minutes(invalidation_hit))


def aggregate(occurrences: list[ResearchOccurrence]) -> dict:
    if not occurrences:
        return {"sample_size": 0, "win_rate": None, "average_win": None, "average_loss": None,
                "expectancy": None, "median_r": None, "average_mfe": None, "average_mae": None}
    wins = [x.outcome_r for x in occurrences if x.outcome_r > 0]
    losses = [-x.outcome_r for x in occurrences if x.outcome_r < 0]
    win_rate = len(wins)/len(occurrences)
    loss_rate = len(losses)/len(occurrences)
    average_win, average_loss = (mean(wins) if wins else 0), (mean(losses) if losses else 0)
    return {"sample_size": len(occurrences), "win_rate": win_rate,
        "average_win": average_win, "average_loss": average_loss,
        "expectancy": win_rate*average_win-loss_rate*average_loss,
        "median_r": median(x.outcome_r for x in occurrences),
        "average_mfe": mean(x.mfe for x in occurrences), "average_mae": mean(x.mae for x in occurrences)}


def replay(symbol: str, bars: list[Bar], periods: ResearchPeriods,
           parameters: EngineParameters = DEFAULT_PARAMETERS) -> dict:
    ordered = sorted(bars, key=lambda x: x.timestamp)
    regular = regular_session_bars(ordered)
    checkpoints = [x.timestamp for x in regular if x.timestamp.astimezone(ET).minute % 5 == 4]
    occurrences, previous, last_key = [], None, None
    for timestamp in checkpoints:
        visible = [x for x in ordered if x.timestamp <= timestamp]  # sole live-engine input: no future bars
        instrument = analyze_symbol(symbol, visible)
        if not instrument.available or instrument.atr_5m is None: continue
        current_day = instrument.as_of.astimezone(ET).date()
        current = [x for x in regular_session_bars(visible) if x.timestamp.astimezone(ET).date() == current_day]
        levels = build_levels(instrument, current, parameters)
        legacy_setup = evaluate_symbol_setup(instrument, levels)
        legacy_plan = build_symbol_trade_plan(instrument, legacy_setup, levels)
        score = score_setup(instrument, legacy_setup, levels, legacy_plan).score
        decision = build_standard_decision(instrument, levels, current, score, previous, parameters)
        previous = decision
        period = periods.period_for(timestamp.astimezone(ET))
        key = (decision.setup_family, decision.direction, decision.trigger)
        if period and decision.setup_state == SetupProgress.CONFIRMED and key != last_key and all(
            x is not None for x in (decision.trigger, decision.invalidation, decision.target_1, decision.target_2)):
            session_future = [x for x in regular if x.timestamp > timestamp and x.timestamp.astimezone(ET).date() == current_day]
            occurrences.append(measure_occurrence(decision, session_future, period))
            last_key = key
    return {"parameters": parameters.as_dict(), "occurrences": [asdict(x) for x in occurrences],
        "development": aggregate([x for x in occurrences if x.period == "DEVELOPMENT"]),
        "validation": aggregate([x for x in occurrences if x.period == "VALIDATION"])}


async def _main(args) -> None:
    if not settings.massive_api_key: raise SystemExit("MASSIVE_API_KEY is required")
    provider = MassiveMarketDataProvider(settings.massive_api_key, settings.massive_base_url)
    try:
        bars = await provider.get_minute_bars(args.symbol, date.fromisoformat(args.dev_start), date.fromisoformat(args.val_end))
    finally:
        await provider.close()
    periods = ResearchPeriods(*(date.fromisoformat(x) for x in (args.dev_start, args.dev_end, args.val_start, args.val_end)))
    print(json.dumps(replay(args.symbol.upper(), bars, periods), default=str, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay one symbol through the live deterministic setup logic")
    parser.add_argument("symbol"); parser.add_argument("--dev-start", required=True); parser.add_argument("--dev-end", required=True)
    parser.add_argument("--val-start", required=True); parser.add_argument("--val-end", required=True)
    asyncio.run(_main(parser.parse_args()))
