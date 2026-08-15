import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.models import (
    ActionNow,
    ContractQuality,
    DecisionQuality,
    OptionsPlanCriteria,
    OptionsRecommendationState,
    SetupFamily,
    SetupProgress,
)
from app.options_intelligence import load_options_plan


class FakePlanProvider:
    def __init__(self, contracts):
        self.contracts = contracts
        self.calls = []

    async def get_stock_snapshot(self, symbol):
        self.calls.append(("snapshot", symbol))
        return {"lastTrade": {"p": 500.0}}

    async def get_active_option_contracts(self, symbol, _as_of):
        self.calls.append(("contracts", symbol))
        return [{"ticker": "O:QQQ260821C00500000", "expiration_date": "2026-08-21"}]

    async def get_options_chain_snapshot(self, symbol, expiration, low, high):
        self.calls.append(("chain", symbol, expiration, low, high))
        return self.contracts


def decision(action, state, quality, direction="LONG", **overrides):
    values = {
        "action_now": action,
        "setup_state": state,
        "quality": quality,
        "direction": direction,
        "setup_family": SetupFamily.BREAKOUT_RETEST,
        "market_price": 500.0,
        "trigger": 501.0,
        "invalidation": 497.0,
        "target_1": 505.0,
        "target_2": 509.0,
        "timestamp": datetime(2026, 8, 14, 14, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def analysis(d):
    return SimpleNamespace(instrument=SimpleNamespace(symbol="QQQ"), decision=d)


def liquid_call(open_interest=700, volume=35):
    return {
        "details": {
            "ticker": "O:QQQ260821C00500000", "contract_type": "call",
            "expiration_date": "2026-08-21", "strike_price": 500,
        },
        "last_quote": {"bid": 5.0, "ask": 5.1, "timeframe": "REAL-TIME"},
        "last_trade": {"price": 5.05, "timeframe": "REAL-TIME"},
        "day": {"volume": volume}, "open_interest": open_interest,
        "implied_volatility": 0.30, "greeks": {"delta": 0.50},
    }


def test_bad_underlying_vetoes_even_an_excellent_looking_contract_without_provider_requests():
    async def scenario():
        provider = FakePlanProvider([liquid_call()])
        result = await load_options_plan(provider, analysis(decision(
            ActionNow.NO_TRADE, SetupProgress.NO_SETUP, DecisionQuality.NO_TRADE, direction="NONE",
        )), OptionsPlanCriteria(), as_of=date(2026, 8, 14))
        assert result.recommendation == OptionsRecommendationState.PASS_UNDERLYING_SETUP_TOO_WEAK
        assert result.underlying_eligibility.eligible_for_contract_evaluation is False
        assert result.contracts_scanned == 0
        assert provider.calls == []
        assert result.underlying_engine_modified is False
        assert result.execution_enabled is False

    asyncio.run(scenario())


def test_waiting_underlying_can_review_a_contract_but_cannot_authorize_entry():
    async def scenario():
        provider = FakePlanProvider([liquid_call()])
        result = await load_options_plan(provider, analysis(decision(
            ActionNow.PREPARE_LONG, SetupProgress.WATCHING, DecisionQuality.A,
        )), OptionsPlanCriteria(), as_of=date(2026, 8, 14))
        assert result.recommendation == OptionsRecommendationState.WAIT_FOR_UNDERLYING_TRIGGER
        assert result.underlying_eligibility.expected_contract_type == "CALL"
        assert result.best_contract is not None
        assert result.best_contract.quality == ContractQuality.EXCELLENT
        assert "wait for the existing underlying trigger" in result.plain_english_action.lower()

    asyncio.run(scenario())


def test_confirmed_underlying_with_missing_open_interest_returns_no_liquidity_pass():
    async def scenario():
        contract = liquid_call(open_interest=None)
        provider = FakePlanProvider([contract])
        result = await load_options_plan(provider, analysis(decision(
            ActionNow.LONG_TRIGGERED, SetupProgress.CONFIRMED, DecisionQuality.A,
        )), OptionsPlanCriteria(), as_of=date(2026, 8, 14))
        assert result.recommendation == OptionsRecommendationState.PASS_POOR_LIQUIDITY
        assert result.best_contract is None
        assert any("Missing open interest" in reason for reason in result.rejected_contract_reasons)

    asyncio.run(scenario())


def test_confirmed_underlying_with_liquid_matching_contract_is_read_only_confirmed_plan():
    async def scenario():
        provider = FakePlanProvider([liquid_call()])
        result = await load_options_plan(provider, analysis(decision(
            ActionNow.LONG_TRIGGERED, SetupProgress.CONFIRMED, DecisionQuality.B,
        )), OptionsPlanCriteria(), as_of=date(2026, 8, 14))
        assert result.recommendation == OptionsRecommendationState.GOOD_TRADE_CONFIRMED
        assert result.best_contract is not None
        assert result.price_iv_assessment == "INSUFFICIENT_DATA"
        assert result.execution_enabled is False
        assert "no options trade" in result.message.lower()

    asyncio.run(scenario())


def test_missing_optional_volume_degrades_quality_without_crashing_or_fabricating_it():
    async def scenario():
        contract = liquid_call(volume=None)
        provider = FakePlanProvider([contract])
        result = await load_options_plan(provider, analysis(decision(
            ActionNow.LONG_TRIGGERED, SetupProgress.CONFIRMED, DecisionQuality.A,
        )), OptionsPlanCriteria(), as_of=date(2026, 8, 14))
        assert result.recommendation == OptionsRecommendationState.GOOD_TRADE_CONFIRMED
        assert result.best_contract is not None
        assert result.best_contract.quality == ContractQuality.GOOD
        assert result.best_contract.contract.volume is None
        assert any("volume was not returned" in reason for reason in result.best_contract.reasons)

    asyncio.run(scenario())


def test_plan_reports_direction_and_does_not_call_mixed_quote_freshness_real_time():
    async def scenario():
        real_time = liquid_call()
        unavailable_quote = liquid_call()
        unavailable_quote["details"] = dict(unavailable_quote["details"], ticker="O:QQQ260821C00501000", strike_price=501)
        unavailable_quote["last_quote"].pop("timeframe")
        provider = FakePlanProvider([real_time, unavailable_quote])
        result = await load_options_plan(provider, analysis(decision(
            ActionNow.PREPARE_LONG, SetupProgress.WATCHING, DecisionQuality.A,
        )), OptionsPlanCriteria(), as_of=date(2026, 8, 14))
        assert result.underlying_eligibility.direction == "LONG"
        assert result.data_status == "AVAILABLE"

    asyncio.run(scenario())


def test_closed_market_allows_research_but_never_confirms_options_entry():
    async def scenario():
        provider = FakePlanProvider([liquid_call()])
        frozen = analysis(decision(
            ActionNow.LONG_TRIGGERED, SetupProgress.CONFIRMED, DecisionQuality.A,
        ))
        frozen.data_status, frozen.market_session = "MARKET_CLOSED", "CLOSED"
        frozen.provider_timestamp = datetime(2026, 8, 14, 23, 59, tzinfo=timezone.utc)
        frozen.analysis_timestamp, frozen.data_age_seconds = datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc), 54_060
        result = await load_options_plan(provider, frozen, OptionsPlanCriteria(), as_of=date(2026, 8, 14))
        assert result.recommendation == OptionsRecommendationState.WAIT_FOR_UNDERLYING_TRIGGER
        assert result.data_status == "MARKET_CLOSED_LATEST_SESSION"
        assert result.best_contract is not None
        assert "wait" in result.plain_english_action.lower()
        assert result.execution_enabled is False

    asyncio.run(scenario())
