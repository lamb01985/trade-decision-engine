import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

import app.backtesting as backtesting
from app.backtesting import (
    BacktestConfig,
    BacktestPeriods,
    DateRange,
    ExecutionAssumptions,
    HistoricalBarCache,
    PeriodLabel,
    TradeStatus,
    run_backtest_for_symbol,
    simulate_trade,
)
from app.market_data.base import MarketDataProvider
from app.models import ActionNow, Bar, DecisionQuality, SetupFamily, SetupProgress, StandardDecision, Trend
from tests.test_v2 import market_bars


UTC = timezone.utc


def decision() -> StandardDecision:
    timestamp = datetime(2026, 8, 12, 13, 34, tzinfo=UTC)
    return StandardDecision(
        symbol="NVDA",
        timestamp=timestamp,
        market_price=100.0,
        direction="LONG",
        setup_family=SetupFamily.BREAKOUT_RETEST,
        setup_state=SetupProgress.CONFIRMED,
        quality=DecisionQuality.B,
        action_now=ActionNow.LONG_TRIGGERED,
        trigger=100.0,
        invalidation=99.0,
        target_1=101.0,
        target_2=102.0,
        structural_risk_reward=1.0,
        nearest_support=99.0,
        nearest_resistance=101.0,
        vwap_state="ABOVE",
        structure_5m=Trend.BULLISH,
        structure_15m=Trend.BULLISH,
        atr=1.0,
        reasoning=["test"],
        missing_confirmation=[],
        do_not_trade_if=[],
        what_changes_my_mind=[],
        state_changed_at=timestamp,
    )


def periods() -> BacktestPeriods:
    return BacktestPeriods(
        development=DateRange(date(2026, 8, 12), date(2026, 8, 12)),
        validation=DateRange(date(2026, 8, 13), date(2026, 8, 13)),
        final_holdout=DateRange(date(2026, 8, 14), date(2026, 8, 14)),
    )


def test_same_bar_stop_and_target_uses_conservative_stop():
    future = [Bar(timestamp=datetime(2026, 8, 12, 13, 35, tzinfo=UTC), open=100, high=101.5, low=98.5, close=100, volume=1000)]

    trade = simulate_trade(decision(), future, PeriodLabel.DEVELOPMENT, ExecutionAssumptions())

    assert trade.status == TradeStatus.INVALIDATED
    assert trade.gross_r == pytest.approx(-1.0)
    assert any(event.kind == "CONSERVATIVE_COLLISION" for event in trade.event_history)


def test_same_bar_stop_and_target_can_be_marked_ambiguous():
    future = [Bar(timestamp=datetime(2026, 8, 12, 13, 35, tzinfo=UTC), open=100, high=101.5, low=98.5, close=100, volume=1000)]

    trade = simulate_trade(
        decision(),
        future,
        PeriodLabel.DEVELOPMENT,
        ExecutionAssumptions(same_bar_policy="AMBIGUOUS"),
    )

    assert trade.status == TradeStatus.AMBIGUOUS
    assert trade.net_r is None


def test_periods_reject_overlapping_holdout_ranges():
    with pytest.raises(ValueError):
        BacktestPeriods(
            development=DateRange(date(2026, 1, 1), date(2026, 1, 31)),
            validation=DateRange(date(2026, 1, 31), date(2026, 2, 28)),
            final_holdout=DateRange(date(2026, 3, 1), date(2026, 3, 31)),
        )


def test_chronological_replay_never_passes_future_bars_to_production_path(monkeypatch):
    seen_lengths = []

    def inspect_visible(symbol, visible, previous, parameters):
        seen_lengths.append(len(visible))
        return None, [], None

    monkeypatch.setattr(backtesting, "_production_decision", inspect_visible)
    config = BacktestConfig(symbols=("NVDA",), periods=periods())

    decisions, confirmations, trades = run_backtest_for_symbol("NVDA", market_bars(), config)

    assert decisions == confirmations == 0
    assert trades == []
    assert seen_lengths[:3] == [5, 10, 15]
    assert seen_lengths == sorted(seen_lengths)
    assert seen_lengths[-1] == len(market_bars())


class FakeProvider(MarketDataProvider):
    def __init__(self):
        self.calls = 0

    async def get_minute_bars(self, symbol, start, end):
        self.calls += 1
        return [Bar(timestamp=datetime(2026, 8, 12, 13, 30, tzinfo=UTC), open=100, high=101, low=99, close=100, volume=1000)]


def test_historical_cache_prevents_repeated_provider_calls(tmp_path):
    async def scenario():
        provider = FakeProvider()
        cache = HistoricalBarCache(tmp_path)
        start = end = date(2026, 8, 12)

        first = await cache.get_or_fetch(provider, "NVDA", start, end)
        second = await cache.get_or_fetch(provider, "NVDA", start, end)

        assert first == second
        assert provider.calls == 1

    asyncio.run(scenario())


def test_target_two_realizes_target_one_tranche_then_remaining_position():
    future = [Bar(timestamp=datetime(2026, 8, 12, 13, 35, tzinfo=UTC), open=100, high=102.5, low=99.8, close=102, volume=1000)]

    trade = simulate_trade(decision(), future, PeriodLabel.DEVELOPMENT, ExecutionAssumptions(target_1_fraction=0.5))

    assert trade.status == TradeStatus.TARGET_2
    assert trade.gross_r == pytest.approx(1.5)
    assert [event.kind for event in trade.event_history].count("TARGET_1") == 1
    assert [event.kind for event in trade.event_history].count("TARGET_2") == 1


def test_final_holdout_is_excluded_from_fetch_range_until_explicitly_enabled():
    baseline = BacktestConfig(symbols=("NVDA",), periods=periods())
    with_holdout = BacktestConfig(symbols=("NVDA",), periods=periods(), include_final_holdout=True)

    assert baseline.fetch_end == date(2026, 8, 13)
    assert with_holdout.fetch_end == date(2026, 8, 14)


def test_confirmed_decision_without_complete_structure_is_recorded_as_skipped():
    incomplete = decision().model_copy(update={"target_2": None})
    future = [Bar(timestamp=datetime(2026, 8, 12, 13, 35, tzinfo=UTC), open=100, high=101, low=99.5, close=100.5, volume=1000)]

    trade = simulate_trade(incomplete, future, PeriodLabel.DEVELOPMENT, ExecutionAssumptions())

    assert trade.status == TradeStatus.SKIPPED
    assert trade.net_r is None
    assert "target_2" in (trade.ambiguity_reason or "")
    assert trade.event_history[-1].kind == "SKIPPED"
