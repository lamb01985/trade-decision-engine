"""Event-driven baseline backtesting built on the production decision engine.

This module intentionally does not define strategy, setup, confirmation, target, or
invalidation logic. At each completed five-minute checkpoint it calls the same
analysis, level, legacy-score, and StandardDecision functions used by
``AnalysisService``. Future bars are used only by the isolated execution simulator
*after* a decision has been recorded.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from enum import Enum
from pathlib import Path
from statistics import mean, median
from typing import Iterable, Literal

from app.engines.decision import build_symbol_trade_plan, evaluate_symbol_setup, score_setup
from app.engines.levels import build_levels
from app.engines.market import ET, analyze_symbol, regular_session_bars
from app.engines.setups import build_standard_decision
from app.market_data.base import MarketDataProvider
from app.models import Bar, SetupProgress, StandardDecision, Trend
from app.parameters import DEFAULT_PARAMETERS, EngineParameters


class BacktestError(ValueError):
    """A baseline configuration or data set cannot support a valid replay."""


class PeriodLabel(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"
    VALIDATION = "VALIDATION"
    FINAL_HOLDOUT = "FINAL_HOLDOUT"


class TradeStatus(str, Enum):
    TARGET_2 = "TARGET_2"
    TARGET_1 = "TARGET_1"
    INVALIDATED = "INVALIDATED"
    END_OF_SESSION = "END_OF_SESSION"
    AMBIGUOUS = "AMBIGUOUS"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise BacktestError("A date range start must be on or before its end")

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


@dataclass(frozen=True)
class BacktestPeriods:
    development: DateRange
    validation: DateRange
    final_holdout: DateRange

    def __post_init__(self) -> None:
        ranges = [self.development, self.validation, self.final_holdout]
        for earlier, later in zip(ranges, ranges[1:]):
            if earlier.end >= later.start:
                raise BacktestError("Development, validation, and final holdout ranges must be non-overlapping and ordered")

    def label_for(self, day: date) -> PeriodLabel | None:
        if self.development.contains(day):
            return PeriodLabel.DEVELOPMENT
        if self.validation.contains(day):
            return PeriodLabel.VALIDATION
        if self.final_holdout.contains(day):
            return PeriodLabel.FINAL_HOLDOUT
        return None


@dataclass(frozen=True)
class ExecutionAssumptions:
    """Execution assumptions expressed without account-size-dependent dollar P/L."""

    entry_rule: Literal["NEXT_BAR_OPEN"] = "NEXT_BAR_OPEN"
    slippage_bps: float = 0.0
    fee_bps_per_side: float = 0.0
    target_1_fraction: float = 0.5
    same_bar_policy: Literal["CONSERVATIVE_STOP", "AMBIGUOUS"] = "CONSERVATIVE_STOP"
    close_unresolved_at_session_end: bool = True

    def __post_init__(self) -> None:
        if self.slippage_bps < 0 or self.fee_bps_per_side < 0:
            raise BacktestError("Slippage and fee assumptions cannot be negative")
        if not 0 <= self.target_1_fraction <= 1:
            raise BacktestError("target_1_fraction must be between 0 and 1")


@dataclass(frozen=True)
class BacktestConfig:
    symbols: tuple[str, ...]
    periods: BacktestPeriods
    execution: ExecutionAssumptions = field(default_factory=ExecutionAssumptions)
    parameters: EngineParameters = DEFAULT_PARAMETERS
    source_bar_minutes: int = 1
    include_final_holdout: bool = False
    minimum_sample_size: int = 30

    def __post_init__(self) -> None:
        if not self.symbols:
            raise BacktestError("At least one symbol is required")
        if self.source_bar_minutes != 1:
            raise BacktestError("The production engine currently requires one-minute source bars; changing the source timeframe would change the strategy")
        if self.minimum_sample_size < 1:
            raise BacktestError("minimum_sample_size must be at least 1")

    @property
    def fetch_start(self) -> date:
        return self.periods.development.start

    @property
    def fetch_end(self) -> date:
        return self.periods.final_holdout.end if self.include_final_holdout else self.periods.validation.end


@dataclass(frozen=True)
class TradeEvent:
    timestamp: datetime
    kind: str
    price: float | None
    detail: str


@dataclass(frozen=True)
class BacktestTrade:
    symbol: str
    period: PeriodLabel
    decision_timestamp: datetime
    entry_timestamp: datetime | None
    setup_family: str
    direction: str
    trigger: float | None
    entry_price: float | None
    invalidation: float | None
    target_1: float | None
    target_2: float | None
    structural_risk_reward: float | None
    trend_5m: str
    trend_15m: str
    vwap_state: str
    relative_volume: float | None
    status: TradeStatus
    gross_r: float | None
    net_r: float | None
    mfe_r: float | None
    mae_r: float | None
    time_to_target_1_minutes: float | None
    time_to_target_2_minutes: float | None
    time_to_invalidation_minutes: float | None
    event_history: tuple[TradeEvent, ...]
    ambiguity_reason: str | None = None


@dataclass(frozen=True)
class BacktestResult:
    config: BacktestConfig
    bars_processed: int
    decisions_evaluated: int
    confirmed_setups: int
    trades: tuple[BacktestTrade, ...]

    @property
    def ambiguous_trades(self) -> tuple[BacktestTrade, ...]:
        return tuple(trade for trade in self.trades if trade.status == TradeStatus.AMBIGUOUS)

    @property
    def generated_trades(self) -> tuple[BacktestTrade, ...]:
        return tuple(trade for trade in self.trades if trade.status not in {TradeStatus.SKIPPED, TradeStatus.AMBIGUOUS})


class HistoricalBarCache:
    """A transparent local cache of provider minute bars, keyed by symbol and date range."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, start: date, end: date) -> Path:
        return self.directory / f"{symbol.upper()}_{start.isoformat()}_{end.isoformat()}_1m.json"

    def load(self, symbol: str, start: date, end: date) -> list[Bar] | None:
        path = self._path(symbol, start, end)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text())
            bars = [Bar(**item) for item in payload]
        except (OSError, ValueError, TypeError) as exc:
            raise BacktestError(f"Historical cache is unreadable for {symbol}: {path}") from exc
        return sorted(bars, key=lambda bar: bar.timestamp)

    def store(self, symbol: str, start: date, end: date, bars: list[Bar]) -> None:
        path = self._path(symbol, start, end)
        payload = [
            {
                "timestamp": bar.timestamp.isoformat(),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in bars
        ]
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")))
        temporary.replace(path)

    async def get_or_fetch(self, provider: MarketDataProvider, symbol: str, start: date, end: date) -> list[Bar]:
        cached = self.load(symbol, start, end)
        if cached is not None:
            return cached
        bars = await provider.get_minute_bars(symbol, start, end)
        ordered = sorted(bars, key=lambda bar: bar.timestamp)
        self.store(symbol, start, end, ordered)
        return ordered


def _production_decision(symbol: str, visible: list[Bar], previous: StandardDecision | None,
                         parameters: EngineParameters) -> tuple[StandardDecision | None, list[Bar], float | None]:
    """Run the live scanner's production setup and StandardDecision path on visible bars only."""
    instrument = analyze_symbol(symbol, visible)
    if not instrument.available or instrument.as_of is None or instrument.atr_5m is None:
        return None, [], None
    current_day = instrument.as_of.astimezone(ET).date()
    current = [
        bar for bar in regular_session_bars(visible)
        if bar.timestamp.astimezone(ET).date() == current_day
    ]
    if not current:
        return None, [], None
    levels = build_levels(instrument, current, parameters)
    legacy_setup = evaluate_symbol_setup(instrument, levels)
    legacy_plan = build_symbol_trade_plan(instrument, legacy_setup, levels)
    score = score_setup(instrument, legacy_setup, levels, legacy_plan).score
    return (build_standard_decision(instrument, levels, current, score, previous, parameters), current,
            instrument.timeframes["5m"].relative_volume)


def _is_checkpoint(bar: Bar) -> bool:
    local = bar.timestamp.astimezone(ET)
    return local.minute % 5 == 4 and time(9, 30) <= local.time() < time(16, 0)


def _slipped(price: float, direction: str, bps: float, entry: bool) -> float:
    """Apply adverse slippage to an entry or exit price."""
    shift = bps / 10_000
    if direction == "LONG":
        return price * (1 + shift if entry else 1 - shift)
    return price * (1 - shift if entry else 1 + shift)


def _r_multiple(entry: float, price: float, risk: float, direction: str) -> float:
    return (price - entry) / risk if direction == "LONG" else (entry - price) / risk


def _minutes_between(start: datetime, end: datetime | None) -> float | None:
    return (end - start).total_seconds() / 60 if end else None


def _hit_prices(bar: Bar, direction: str, stop: float, target_1: float, target_2: float) -> tuple[bool, bool, bool]:
    if direction == "LONG":
        return bar.low <= stop, bar.high >= target_1, bar.high >= target_2
    return bar.high >= stop, bar.low <= target_1, bar.low <= target_2


def _entry_is_invalid(entry: float, direction: str, stop: float, target_1: float) -> bool:
    if direction == "LONG":
        return entry <= stop or entry >= target_1
    return entry >= stop or entry <= target_1


def simulate_trade(decision: StandardDecision, future: list[Bar], period: PeriodLabel,
                   assumptions: ExecutionAssumptions, relative_volume: float | None = None) -> BacktestTrade:
    """Simulate one confirmed decision using only future session bars for execution.

    A decision is made at a completed five-minute checkpoint. Entry occurs only at the
    following minute bar's open, avoiding an impossible same-bar fill. If OHLC cannot
    order an intra-bar stop and target event, the configured conservative-stop or
    ambiguous-trade policy is used.
    """
    direction = decision.direction
    trigger, stop, target_1, target_2 = decision.trigger, decision.invalidation, decision.target_1, decision.target_2
    history: list[TradeEvent] = [TradeEvent(decision.timestamp, "DECISION", decision.market_price, "Production StandardDecision became CONFIRMED")]
    base = dict(
        symbol=decision.symbol, period=period, decision_timestamp=decision.timestamp,
        setup_family=decision.setup_family.value, direction=direction, trigger=trigger,
        invalidation=stop, target_1=target_1, target_2=target_2,
        structural_risk_reward=decision.structural_risk_reward,
        trend_5m=decision.structure_5m.value, trend_15m=decision.structure_15m.value,
        vwap_state=decision.vwap_state, relative_volume=relative_volume,
    )
    if any(value is None for value in (trigger, stop, target_1, target_2)):
        missing = ", ".join(name for name, value in (("trigger", trigger), ("invalidation", stop), ("target_1", target_1), ("target_2", target_2)) if value is None)
        history.append(TradeEvent(decision.timestamp, "SKIPPED", None, f"Confirmed setup had incomplete mapped structure: {missing}"))
        return BacktestTrade(**base, entry_timestamp=None, entry_price=None, status=TradeStatus.SKIPPED,
            gross_r=None, net_r=None, mfe_r=None, mae_r=None, time_to_target_1_minutes=None,
            time_to_target_2_minutes=None, time_to_invalidation_minutes=None,
            event_history=tuple(history), ambiguity_reason=f"Non-executable confirmed setup: missing {missing}")
    assert trigger is not None and stop is not None and target_1 is not None and target_2 is not None
    if not future:
        return BacktestTrade(**base, entry_timestamp=None, entry_price=None, status=TradeStatus.SKIPPED,
            gross_r=None, net_r=None, mfe_r=None, mae_r=None, time_to_target_1_minutes=None,
            time_to_target_2_minutes=None, time_to_invalidation_minutes=None,
            event_history=tuple(history), ambiguity_reason="No subsequent regular-session bar exists for a valid next-bar entry")

    entry_bar = future[0]
    entry = _slipped(entry_bar.open, direction, assumptions.slippage_bps, entry=True)
    if _entry_is_invalid(entry, direction, stop, target_1):
        history.append(TradeEvent(entry_bar.timestamp, "SKIPPED", entry, "Next-bar open was beyond the invalidation or first target"))
        return BacktestTrade(**base, entry_timestamp=entry_bar.timestamp, entry_price=entry, status=TradeStatus.SKIPPED,
            gross_r=None, net_r=None, mfe_r=None, mae_r=None, time_to_target_1_minutes=None,
            time_to_target_2_minutes=None, time_to_invalidation_minutes=None,
            event_history=tuple(history), ambiguity_reason="No valid executable entry after confirmation")

    risk = abs(entry - stop)
    if risk <= 0:
        raise BacktestError("A valid executable trade requires positive entry-to-invalidation risk")
    history.append(TradeEvent(entry_bar.timestamp, "ENTRY", entry, "Next-bar-open entry with adverse slippage"))
    gross_r = 0.0
    fee_r = entry * (assumptions.fee_bps_per_side / 10_000) / risk
    remaining = 1.0
    t1_time = t2_time = stop_time = None
    mfe = mae = 0.0
    exit_price: float | None = None
    status: TradeStatus | None = None

    for bar in future:
        favorable = bar.high - entry if direction == "LONG" else entry - bar.low
        adverse = entry - bar.low if direction == "LONG" else bar.high - entry
        mfe = max(mfe, favorable / risk)
        mae = max(mae, adverse / risk)
        hit_stop, hit_t1, hit_t2 = _hit_prices(bar, direction, stop, target_1, target_2)
        conflicting = hit_stop and (hit_t1 or hit_t2)
        if conflicting:
            if assumptions.same_bar_policy == "AMBIGUOUS":
                history.append(TradeEvent(bar.timestamp, "AMBIGUOUS", None, "OHLC cannot order stop and target touch in the same bar"))
                return BacktestTrade(**base, entry_timestamp=entry_bar.timestamp, entry_price=entry,
                    status=TradeStatus.AMBIGUOUS, gross_r=None, net_r=None, mfe_r=mfe, mae_r=mae,
                    time_to_target_1_minutes=_minutes_between(decision.timestamp, t1_time),
                    time_to_target_2_minutes=_minutes_between(decision.timestamp, t2_time),
                    time_to_invalidation_minutes=_minutes_between(decision.timestamp, bar.timestamp),
                    event_history=tuple(history), ambiguity_reason="Stop and target were both reachable inside one OHLC bar")
            hit_t1 = hit_t2 = False
            history.append(TradeEvent(bar.timestamp, "CONSERVATIVE_COLLISION", stop, "Stop assumed before target because OHLC ordering is unknown"))

        if hit_t1 and t1_time is None:
            t1_time = bar.timestamp
            fraction = assumptions.target_1_fraction
            if fraction:
                fill = _slipped(target_1, direction, assumptions.slippage_bps, entry=False)
                gross_r += fraction * _r_multiple(entry, fill, risk, direction)
                fee_r += fraction * fill * (assumptions.fee_bps_per_side / 10_000) / risk
                remaining -= fraction
                history.append(TradeEvent(bar.timestamp, "TARGET_1", fill, f"Exited {fraction:.0%} of original size"))
            if remaining <= 0:
                status, exit_price = TradeStatus.TARGET_1, target_1
                break

        if hit_t2:
            if t1_time is None:
                t1_time = bar.timestamp
                fraction = assumptions.target_1_fraction
                if fraction:
                    t1_fill = _slipped(target_1, direction, assumptions.slippage_bps, entry=False)
                    gross_r += fraction * _r_multiple(entry, t1_fill, risk, direction)
                    fee_r += fraction * t1_fill * (assumptions.fee_bps_per_side / 10_000) / risk
                    remaining -= fraction
                    history.append(TradeEvent(bar.timestamp, "TARGET_1", t1_fill, f"Exited {fraction:.0%} of original size before target 2"))
            t2_time = bar.timestamp
            fill = _slipped(target_2, direction, assumptions.slippage_bps, entry=False)
            gross_r += remaining * _r_multiple(entry, fill, risk, direction)
            fee_r += remaining * fill * (assumptions.fee_bps_per_side / 10_000) / risk
            remaining = 0.0
            status, exit_price = TradeStatus.TARGET_2, target_2
            history.append(TradeEvent(bar.timestamp, "TARGET_2", fill, "Exited remaining size"))
            break

        if hit_stop:
            stop_time = bar.timestamp
            fill = _slipped(stop, direction, assumptions.slippage_bps, entry=False)
            gross_r += remaining * _r_multiple(entry, fill, risk, direction)
            fee_r += remaining * fill * (assumptions.fee_bps_per_side / 10_000) / risk
            remaining = 0.0
            status, exit_price = TradeStatus.INVALIDATED, stop
            history.append(TradeEvent(bar.timestamp, "INVALIDATED", fill, "Exited remaining size at invalidation"))
            break

    if status is None:
        final_bar = future[-1]
        if assumptions.close_unresolved_at_session_end:
            fill = _slipped(final_bar.close, direction, assumptions.slippage_bps, entry=False)
            gross_r += remaining * _r_multiple(entry, fill, risk, direction)
            fee_r += remaining * fill * (assumptions.fee_bps_per_side / 10_000) / risk
            history.append(TradeEvent(final_bar.timestamp, "END_OF_SESSION", fill, "Closed unresolved remainder at session end"))
            status, exit_price = TradeStatus.END_OF_SESSION, fill
        else:
            history.append(TradeEvent(final_bar.timestamp, "OPEN_AT_SESSION_END", final_bar.close, "No forced end-of-session close"))
            status, exit_price = TradeStatus.END_OF_SESSION, None

    return BacktestTrade(**base, entry_timestamp=entry_bar.timestamp, entry_price=entry, status=status,
        gross_r=gross_r, net_r=gross_r-fee_r, mfe_r=mfe, mae_r=mae,
        time_to_target_1_minutes=_minutes_between(decision.timestamp, t1_time),
        time_to_target_2_minutes=_minutes_between(decision.timestamp, t2_time),
        time_to_invalidation_minutes=_minutes_between(decision.timestamp, stop_time),
        event_history=tuple(history), ambiguity_reason=None)


def _trade_fingerprint(decision: StandardDecision) -> tuple:
    return (decision.setup_family.value, decision.direction, round(decision.trigger or 0, 8),
            round(decision.invalidation or 0, 8), round(decision.target_1 or 0, 8),
            round(decision.target_2 or 0, 8))


def run_backtest_for_symbol(symbol: str, bars: list[Bar], config: BacktestConfig) -> tuple[int, int, list[BacktestTrade]]:
    """Chronologically replay one symbol and return decision count, confirmations, and event trades."""
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    regular = regular_session_bars(ordered)
    checkpoints = [bar.timestamp for bar in regular if _is_checkpoint(bar)]
    previous: StandardDecision | None = None
    active_fingerprint: tuple | None = None
    decisions_evaluated = confirmed = 0
    trades: list[BacktestTrade] = []

    for timestamp in checkpoints:
        local_day = timestamp.astimezone(ET).date()
        period = config.periods.label_for(local_day)
        if period is None or (period == PeriodLabel.FINAL_HOLDOUT and not config.include_final_holdout):
            continue
        visible = [bar for bar in ordered if bar.timestamp <= timestamp]
        decision, _, relative_volume = _production_decision(symbol, visible, previous, config.parameters)
        if decision is None:
            continue
        decisions_evaluated += 1
        previous = decision
        if decision.setup_state != SetupProgress.CONFIRMED:
            active_fingerprint = None
            continue
        confirmed += 1
        fingerprint = _trade_fingerprint(decision)
        if fingerprint == active_fingerprint:
            continue
        active_fingerprint = fingerprint
        session_future = [
            bar for bar in regular
            if bar.timestamp > timestamp and bar.timestamp.astimezone(ET).date() == local_day
        ]
        trades.append(simulate_trade(decision, session_future, period, config.execution, relative_volume))
    return decisions_evaluated, confirmed, trades


async def run_backtest(provider: MarketDataProvider, cache: HistoricalBarCache,
                       config: BacktestConfig) -> BacktestResult:
    """Fetch/cache source data, then run an event-driven baseline with no optimization."""
    bars_processed = decisions = confirmations = 0
    trades: list[BacktestTrade] = []
    for symbol in tuple(dict.fromkeys(item.strip().upper() for item in config.symbols)):
        bars = await cache.get_or_fetch(provider, symbol, config.fetch_start, config.fetch_end)
        bars_processed += len(bars)
        symbol_decisions, symbol_confirmations, symbol_trades = run_backtest_for_symbol(symbol, bars, config)
        decisions += symbol_decisions
        confirmations += symbol_confirmations
        trades.extend(symbol_trades)
    return BacktestResult(config=config, bars_processed=bars_processed, decisions_evaluated=decisions,
        confirmed_setups=confirmations, trades=tuple(trades))


def _safe_mean(values: Iterable[float]) -> float | None:
    materialized = list(values)
    return mean(materialized) if materialized else None


def aggregate_trades(trades: Iterable[BacktestTrade]) -> dict:
    all_trades = list(trades)
    measured = [trade for trade in all_trades if trade.net_r is not None]
    outcomes = [trade.net_r for trade in measured if trade.net_r is not None]
    wins = [value for value in outcomes if value > 0]
    losses = [-value for value in outcomes if value < 0]
    equity, peak, maximum_drawdown = 0.0, 0.0, 0.0
    for value in outcomes:
        equity += value
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak-equity)
    gross_profit, gross_loss = sum(wins), sum(losses)
    return {
        "trades": len(measured),
        "skipped": sum(trade.status == TradeStatus.SKIPPED for trade in all_trades),
        "ambiguous": sum(trade.status == TradeStatus.AMBIGUOUS for trade in all_trades),
        "win_rate": len(wins)/len(measured) if measured else None,
        "loss_rate": len(losses)/len(measured) if measured else None,
        "average_win_r": _safe_mean(wins),
        "average_loss_r": _safe_mean(losses),
        "expectancy_r": _safe_mean(outcomes),
        "median_r": median(outcomes) if outcomes else None,
        "profit_factor": gross_profit/gross_loss if gross_loss else (None if not gross_profit else float("inf")),
        "maximum_drawdown_r": maximum_drawdown if measured else None,
        "average_mfe_r": _safe_mean(trade.mfe_r for trade in measured if trade.mfe_r is not None),
        "average_mae_r": _safe_mean(trade.mae_r for trade in measured if trade.mae_r is not None),
    }


def grouped_results(trades: Iterable[BacktestTrade], field_name: str, minimum_sample_size: int) -> dict[str, dict]:
    return grouped_results_by_key(trades, lambda trade: str(getattr(trade, field_name)), minimum_sample_size)


def grouped_results_by_key(trades: Iterable[BacktestTrade], key, minimum_sample_size: int) -> dict[str, dict]:
    groups: dict[str, list[BacktestTrade]] = {}
    for trade in trades:
        groups.setdefault(key(trade), []).append(trade)
    return {
        name: {**aggregate_trades(group), "insufficient_sample": len([trade for trade in group if trade.net_r is not None]) < minimum_sample_size}
        for name, group in sorted(groups.items())
    }


def time_of_day_bucket(trade: BacktestTrade) -> str:
    local = trade.decision_timestamp.astimezone(ET)
    return f"{local.hour:02d}:{(local.minute // 30) * 30:02d} ET"


def relative_volume_bucket(trade: BacktestTrade) -> str:
    value = trade.relative_volume
    if value is None:
        return "UNAVAILABLE"
    if value < 0.8:
        return "<0.80"
    if value < 1.2:
        return "0.80–1.19"
    if value < 2.0:
        return "1.20–1.99"
    return "≥2.00"


def structural_risk_reward_bucket(trade: BacktestTrade) -> str:
    value = trade.structural_risk_reward
    if value is None:
        return "UNAVAILABLE"
    if value < 1.5:
        return "<1.50"
    if value < 2.0:
        return "1.50–1.99"
    if value < 3.0:
        return "2.00–2.99"
    return "≥3.00"


def serialize_trade(trade: BacktestTrade) -> dict:
    payload = asdict(trade)
    payload["period"] = trade.period.value
    payload["status"] = trade.status.value
    payload["event_history"] = [
        {"timestamp": event.timestamp.isoformat(), "kind": event.kind, "price": event.price, "detail": event.detail}
        for event in trade.event_history
    ]
    return payload


def build_report(result: BacktestResult) -> dict:
    """Return JSON-serializable baseline statistics without changing strategy rules."""
    generated = result.generated_trades
    return {
        "configuration": {
            "symbols": list(result.config.symbols),
            "development": asdict(result.config.periods.development),
            "validation": asdict(result.config.periods.validation),
            "final_holdout": asdict(result.config.periods.final_holdout),
            "include_final_holdout": result.config.include_final_holdout,
            "execution": asdict(result.config.execution),
            "parameters": result.config.parameters.as_dict(),
        },
        "bars_processed": result.bars_processed,
        "decisions_evaluated": result.decisions_evaluated,
        "confirmed_setups": result.confirmed_setups,
        "trades_generated": len(generated),
        "overall": aggregate_trades(result.trades),
        "by_setup_family": grouped_results(result.trades, "setup_family", result.config.minimum_sample_size),
        "by_direction": grouped_results(result.trades, "direction", result.config.minimum_sample_size),
        "by_ticker": grouped_results(result.trades, "symbol", result.config.minimum_sample_size),
        "by_5m_trend": grouped_results(result.trades, "trend_5m", result.config.minimum_sample_size),
        "by_15m_trend": grouped_results(result.trades, "trend_15m", result.config.minimum_sample_size),
        "by_vwap_state": grouped_results(result.trades, "vwap_state", result.config.minimum_sample_size),
        "by_time_of_day": grouped_results_by_key(result.trades, time_of_day_bucket, result.config.minimum_sample_size),
        "by_relative_volume": grouped_results_by_key(result.trades, relative_volume_bucket, result.config.minimum_sample_size),
        "by_structural_risk_reward": grouped_results_by_key(result.trades, structural_risk_reward_bucket, result.config.minimum_sample_size),
        "ambiguous_trades": [serialize_trade(trade) for trade in result.ambiguous_trades],
        "representative_trades": [serialize_trade(trade) for trade in result.trades[:5]],
    }


def render_markdown_report(report: dict) -> str:
    """Render a compact human-auditable baseline report from ``build_report`` output."""
    overall = report["overall"]
    def number(value: float | int | None, digits: int = 3) -> str:
        if value is None:
            return "N/A"
        if value == float("inf"):
            return "∞"
        return f"{value:.{digits}f}" if isinstance(value, float) else str(value)

    lines = [
        "# Baseline Backtest Report",
        "",
        "> This is a deterministic historical simulation. It is not a performance guarantee and it does not change the production trading rules.",
        "",
        "## Run summary",
        "",
        f"- Historical bars processed: **{report['bars_processed']}**",
        f"- Production decisions evaluated: **{report['decisions_evaluated']}**",
        f"- Confirmed setup observations: **{report['confirmed_setups']}**",
        f"- Generated executable trades: **{report['trades_generated']}**",
        f"- Ambiguous trades: **{overall['ambiguous']}**",
        "",
        "## Overall system",
        "",
        "| Trades | Win rate | Average win (R) | Average loss (R) | Expectancy (R) | Profit factor | Maximum drawdown (R) |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {overall['trades']} | {number(overall['win_rate'] * 100 if overall['win_rate'] is not None else None, 1)}% | {number(overall['average_win_r'])} | {number(overall['average_loss_r'])} | {number(overall['expectancy_r'])} | {number(overall['profit_factor'])} | {number(overall['maximum_drawdown_r'])} |",
        "",
        "## By setup family",
        "",
        "| Setup | N | Win % | Average win R | Average loss R | Expectancy R | Profit factor | Insufficient sample |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for setup, metrics in report["by_setup_family"].items():
        lines.append(
            f"| {setup} | {metrics['trades']} | {number(metrics['win_rate'] * 100 if metrics['win_rate'] is not None else None, 1)}% | {number(metrics['average_win_r'])} | {number(metrics['average_loss_r'])} | {number(metrics['expectancy_r'])} | {number(metrics['profit_factor'])} | {'Yes' if metrics['insufficient_sample'] else 'No'} |"
        )
    lines.extend(["", "## Representative event histories", ""])
    for trade in report["representative_trades"]:
        lines.append(f"### {trade['symbol']} — {trade['setup_family']} — {trade['status']}")
        lines.append("")
        lines.append(f"Decision: `{trade['decision_timestamp']}`; trigger `{trade['trigger']}`; entry `{trade['entry_price']}`; invalidation `{trade['invalidation']}`; targets `{trade['target_1']}` / `{trade['target_2']}`; net R `{trade['net_r']}`.")
        for event in trade["event_history"]:
            lines.append(f"- `{event['timestamp']}` — **{event['kind']}** at `{event['price']}`: {event['detail']}")
        lines.append("")
    return "\n".join(lines)


async def run_and_write_report(provider: MarketDataProvider, cache_directory: Path, config: BacktestConfig,
                               output_json: Path, output_markdown: Path) -> BacktestResult:
    result = await run_backtest(provider, HistoricalBarCache(cache_directory), config)
    report = build_report(result)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, default=str, indent=2))
    output_markdown.write_text(render_markdown_report(report))
    return result


def run(coro):
    """Convenience wrapper kept outside production request handling."""
    return asyncio.run(coro)
