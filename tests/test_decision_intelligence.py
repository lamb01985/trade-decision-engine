from datetime import datetime, timezone

import pytest

from app.decision_intelligence import FAMILY_COMPONENTS, build_decision_intelligence
from app.models import (ActionNow, DecisionQuality, LevelImportance, PriceLevel,
    RecommendedAction, SetupFamily, SetupProgress, StandardDecision, Trend)


def decision(state=SetupProgress.WATCHING, family=SetupFamily.BREAKOUT_RETEST,
             missing=None, direction="LONG"):
    action = {
        SetupProgress.NO_SETUP: ActionNow.NO_TRADE,
        SetupProgress.FORMING: ActionNow.WAIT,
        SetupProgress.WATCHING: ActionNow.PREPARE_LONG,
        SetupProgress.TRIGGERED: ActionNow.PREPARE_LONG,
        SetupProgress.CONFIRMED: ActionNow.LONG_TRIGGERED,
        SetupProgress.INVALIDATED: ActionNow.NO_TRADE,
        SetupProgress.TOO_LATE: ActionNow.DO_NOT_CHASE,
    }[state]
    return StandardDecision(symbol="NVDA", timestamp=datetime.now(timezone.utc), market_price=100,
        direction="NONE" if state == SetupProgress.NO_SETUP else direction,
        setup_family=SetupFamily.NONE if state == SetupProgress.NO_SETUP else family,
        setup_state=state, quality=DecisionQuality.NO_TRADE if state in {
            SetupProgress.NO_SETUP, SetupProgress.INVALIDATED, SetupProgress.TOO_LATE} else DecisionQuality.A,
        action_now=action, trigger=None if state == SetupProgress.NO_SETUP else 101,
        invalidation=None if state == SetupProgress.NO_SETUP else 99,
        target_1=None if state == SetupProgress.NO_SETUP else 103,
        target_2=None if state == SetupProgress.NO_SETUP else 105,
        structural_risk_reward=None if state == SetupProgress.NO_SETUP else 2,
        vwap_state="ABOVE", structure_5m=Trend.BULLISH, structure_15m=Trend.BULLISH,
        reasoning=["existing deterministic setup evidence"],
        missing_confirmation=list(missing or []), do_not_trade_if=[], what_changes_my_mind=[],
        state_changed_at=datetime.now(timezone.utc))


def levels():
    return [
        PriceLevel(price=99, types=["SWING_LOW"], importance=LevelImportance.SUPPORT,
                   distance=-1, distance_pct=-1, position="BELOW", confluence=2),
        PriceLevel(price=101, types=["OPENING_RANGE_HIGH"], importance=LevelImportance.RESISTANCE,
                   distance=1, distance_pct=1, position="ABOVE", confluence=1),
        PriceLevel(price=103, types=["PRIOR_DAY_HIGH"], importance=LevelImportance.MAJOR_RESISTANCE,
                   distance=3, distance_pct=3, position="ABOVE", confluence=2),
    ]


@pytest.mark.parametrize(("state", "expected"), [
    (SetupProgress.NO_SETUP, RecommendedAction.IGNORE),
    (SetupProgress.FORMING, RecommendedAction.MONITOR),
    (SetupProgress.WATCHING, RecommendedAction.MONITOR),
    (SetupProgress.TRIGGERED, RecommendedAction.WAIT_FOR_CONFIRMATION),
    (SetupProgress.CONFIRMED, RecommendedAction.READY),
    (SetupProgress.INVALIDATED, RecommendedAction.INVALIDATED),
    (SetupProgress.TOO_LATE, RecommendedAction.EXPIRED),
])
def test_recommended_action_is_derived_from_existing_lifecycle(state, expected):
    contract = build_decision_intelligence(decision(state), levels(),
        data_state="LIVE_CURRENT", market_session="REGULAR")
    assert contract.recommended_action == expected


def test_touch_or_trigger_without_confirmation_can_never_be_ready():
    contract = build_decision_intelligence(decision(SetupProgress.TRIGGERED,
        missing=["relative-volume confirmation"]), levels(),
        data_state="LIVE_CURRENT", market_session="REGULAR")
    assert contract.recommended_action == RecommendedAction.WAIT_FOR_CONFIRMATION
    assert contract.recommended_action != RecommendedAction.READY


@pytest.mark.parametrize(("data_state", "session"), [
    ("LIVE_STALE", "REGULAR"), ("DATA_ERROR", "REGULAR"),
    ("LIVE_CURRENT", "PREMARKET"), ("MARKET_CLOSED_LAST_SESSION", "CLOSED"),
])
def test_degraded_or_non_regular_data_cannot_create_ready(data_state, session):
    contract = build_decision_intelligence(decision(SetupProgress.CONFIRMED), levels(),
        data_state=data_state, market_session=session)
    assert contract.recommended_action != RecommendedAction.READY
    if data_state in {"LIVE_STALE", "DATA_ERROR"}:
        assert contract.recommended_action == RecommendedAction.DATA_UNAVAILABLE


def test_no_trade_and_data_unavailable_are_distinct():
    no_trade = build_decision_intelligence(decision(SetupProgress.NO_SETUP), levels(),
        data_state="LIVE_CURRENT", market_session="REGULAR")
    unavailable = build_decision_intelligence(decision(SetupProgress.NO_SETUP), levels(),
        data_state="DATA_ERROR", market_session="REGULAR")
    assert no_trade.recommended_action == RecommendedAction.IGNORE
    assert unavailable.recommended_action == RecommendedAction.DATA_UNAVAILABLE
    assert no_trade.recommended_action_label != unavailable.recommended_action_label


def test_setup_families_keep_distinct_confirmation_components():
    breakout = build_decision_intelligence(decision(SetupProgress.TRIGGERED,
        SetupFamily.BREAKOUT_RETEST, ["5m acceptance/retest", "relative-volume confirmation"]),
        levels(), data_state="LIVE_CURRENT", market_session="REGULAR")
    pullback = build_decision_intelligence(decision(SetupProgress.TRIGGERED,
        SetupFamily.TREND_PULLBACK, ["relative-volume confirmation"]),
        levels(), data_state="LIVE_CURRENT", market_session="REGULAR")
    assert [item.id for item in breakout.confirmation_components] != [
        item.id for item in pullback.confirmation_components]
    assert set(FAMILY_COMPONENTS) == set(SetupFamily) - {SetupFamily.NONE}


def test_setup_specific_pattern_passes_are_not_collapsed_into_generic_pending():
    failed_breakout = build_decision_intelligence(decision(SetupProgress.TRIGGERED,
        SetupFamily.FAILED_BREAKOUT, ["relative-volume confirmation"], direction="SHORT"),
        levels(), data_state="LIVE_CURRENT", market_session="REGULAR")
    breakout_retest = build_decision_intelligence(decision(SetupProgress.TRIGGERED,
        SetupFamily.BREAKOUT_RETEST, ["5m acceptance/retest", "relative-volume confirmation"]),
        levels(), data_state="LIVE_CURRENT", market_session="REGULAR")
    assert failed_breakout.confirmation_components[0].state.value == "PASS"
    assert breakout_retest.confirmation_components[0].state.value == "PENDING"


def test_confirmation_distinguishes_pass_pending_and_unavailable():
    confirmed = build_decision_intelligence(decision(SetupProgress.CONFIRMED), levels(),
        data_state="LIVE_CURRENT", market_session="REGULAR")
    pending = build_decision_intelligence(decision(SetupProgress.TRIGGERED,
        missing=["relative-volume confirmation"]), levels(),
        data_state="LIVE_CURRENT", market_session="REGULAR")
    no_setup = build_decision_intelligence(decision(SetupProgress.NO_SETUP), levels(),
        data_state="LIVE_CURRENT", market_session="REGULAR")
    unavailable = build_decision_intelligence(decision(SetupProgress.TRIGGERED, missing=[]), levels(),
        data_state="LIVE_CURRENT", market_session="REGULAR")
    assert {item.state.value for item in confirmed.confirmation_components} == {"PASS"}
    assert "PENDING" in {item.state.value for item in pending.confirmation_components}
    assert "UNAVAILABLE" in {item.state.value for item in unavailable.confirmation_components}
    assert no_setup.confirmation_components == []


def test_structured_zone_invalidation_requirements_and_level_roles():
    contract = build_decision_intelligence(decision(SetupProgress.TRIGGERED,
        missing=["relative-volume confirmation"]), levels(),
        data_state="LIVE_CURRENT", market_session="REGULAR")
    assert contract.decision_zone.lower_bound == contract.decision_zone.upper_bound == 101
    assert contract.invalidation.price == 99
    assert contract.invalidation.condition == "Price trades below the mapped invalidation"
    assert contract.next_state_requirements[0].target_state == "CONFIRMED"
    trigger = next(item for item in contract.levels if "TRIGGER" in item.categories)
    structural = next(item for item in contract.levels if item.price == 103)
    assert trigger.currently_interacting is True and trigger.setup_specific is True
    assert structural.currently_interacting is False


def test_runtime_contract_rebuilds_from_persisted_decision_without_schema_duplication():
    original = decision(SetupProgress.TRIGGERED, missing=["relative-volume confirmation"])
    restored = StandardDecision.model_validate(original.model_dump(mode="json"))
    first = build_decision_intelligence(original, levels(), data_state="LIVE_CURRENT",
        market_session="REGULAR")
    second = build_decision_intelligence(restored, levels(), data_state="LIVE_CURRENT",
        market_session="REGULAR")
    assert second == first
    assert "decision_intelligence" not in original.model_dump()
