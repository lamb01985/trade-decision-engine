"""Derived trader-facing contract over frozen deterministic decision semantics."""
from __future__ import annotations

import re

from app.models import (ConfirmationComponent, ConfirmationComponentState, DecisionIntelligence,
    DecisionLevelMetadata, DecisionZone, InvalidationRule, PriceLevel, RecommendedAction,
    SetupFamily, SetupProgress, StandardDecision)


FAMILY_COMPONENTS: dict[SetupFamily, tuple[tuple[str, str], ...]] = {
    SetupFamily.OPENING_RANGE_BREAKOUT: (("acceptance_retest", "5m acceptance/retest above opening-range high"), ("relative_volume", "Relative-volume confirmation")),
    SetupFamily.OPENING_RANGE_BREAKDOWN: (("acceptance_retest", "5m acceptance/retest below opening-range low"), ("relative_volume", "Relative-volume confirmation")),
    SetupFamily.BREAKOUT_RETEST: (("acceptance_retest", "5m acceptance/retest above mapped resistance"), ("relative_volume", "Relative-volume confirmation")),
    SetupFamily.BREAKDOWN_RETEST: (("acceptance_retest", "5m acceptance/retest below mapped support"), ("relative_volume", "Relative-volume confirmation")),
    SetupFamily.VWAP_RECLAIM: (("vwap_hold_retest", "VWAP hold/retest"), ("relative_volume", "Relative-volume confirmation")),
    SetupFamily.VWAP_REJECTION: (("vwap_rejection", "VWAP rejection and lower close"), ("relative_volume", "Relative-volume confirmation")),
    SetupFamily.FAILED_BREAKOUT: (("failed_reclaim", "Failure to reclaim mapped resistance"), ("relative_volume", "Relative-volume confirmation")),
    SetupFamily.FAILED_BREAKDOWN: (("held_reclaim", "Hold above reclaimed mapped support"), ("relative_volume", "Relative-volume confirmation")),
    SetupFamily.TREND_PULLBACK: (("trend_resume", "Aligned trend pullback holds and resumes"), ("relative_volume", "Relative-volume confirmation")),
    SetupFamily.MOMENTUM_CONTINUATION: (("pause_break", "Aligned trend closes through the pause"), ("relative_volume", "Relative-volume confirmation")),
    SetupFamily.COMPRESSION_BREAK: (("range_acceptance", "Acceptance outside the compressed range"), ("relative_volume", "Relative-volume confirmation")),
}


def _action(decision: StandardDecision, data_state: str, market_session: str) -> RecommendedAction:
    if data_state in {"DATA_ERROR", "LIVE_STALE"}:
        return RecommendedAction.DATA_UNAVAILABLE
    state = decision.setup_state
    if state == SetupProgress.INVALIDATED:
        return RecommendedAction.INVALIDATED
    if state == SetupProgress.TOO_LATE:
        return RecommendedAction.EXPIRED
    if state == SetupProgress.NO_SETUP:
        return RecommendedAction.IGNORE
    if state == SetupProgress.FORMING:
        return RecommendedAction.MONITOR
    if state == SetupProgress.WATCHING:
        return RecommendedAction.MONITOR
    if state == SetupProgress.TRIGGERED:
        return RecommendedAction.WAIT_FOR_CONFIRMATION
    if state == SetupProgress.CONFIRMED:
        return (RecommendedAction.READY if data_state == "LIVE_CURRENT" and market_session == "REGULAR"
                else RecommendedAction.MONITOR)
    return RecommendedAction.DATA_UNAVAILABLE


ACTION_LABELS = {
    RecommendedAction.IGNORE: "No qualifying setup. Ignore for now.",
    RecommendedAction.MONITOR: "Monitor the setup and wait for its required evidence.",
    RecommendedAction.WAIT_FOR_CONFIRMATION: "Price has triggered; wait for setup-specific confirmation.",
    RecommendedAction.READY: "Required setup confirmation is complete. Revalidate risk and extension.",
    RecommendedAction.MANAGE: "Manage the active position against its plan.",
    RecommendedAction.INVALIDATED: "The setup is invalidated. Do not enter.",
    RecommendedAction.EXPIRED: "The entry is no longer valid. Wait for new structure.",
    RecommendedAction.DATA_UNAVAILABLE: "Decision unavailable until current valid market data returns.",
}


def _component_state(component_id: str, decision: StandardDecision) -> ConfirmationComponentState:
    if decision.setup_state == SetupProgress.CONFIRMED:
        return ConfirmationComponentState.PASS
    missing = " ".join(decision.missing_confirmation).lower()
    if component_id == "relative_volume":
        if decision.setup_state in {SetupProgress.FORMING, SetupProgress.WATCHING} and "relative-volume" in missing:
            return ConfirmationComponentState.PENDING
        return ConfirmationComponentState.PENDING if "volume" in missing else ConfirmationComponentState.UNAVAILABLE
    pattern_complete_on_detection = {
        SetupFamily.VWAP_REJECTION, SetupFamily.FAILED_BREAKOUT, SetupFamily.FAILED_BREAKDOWN,
        SetupFamily.TREND_PULLBACK, SetupFamily.MOMENTUM_CONTINUATION,
        SetupFamily.COMPRESSION_BREAK,
    }
    if decision.setup_state == SetupProgress.TRIGGERED and decision.setup_family in pattern_complete_on_detection:
        return ConfirmationComponentState.PASS
    tokens = {
        "acceptance_retest": ("acceptance", "retest"), "vwap_hold_retest": ("vwap hold", "retest"),
        "vwap_rejection": ("vwap",), "failed_reclaim": ("reclaim",), "held_reclaim": ("reclaim",),
        "trend_resume": ("confirmation sequence",), "pause_break": ("confirmation sequence",),
        "range_acceptance": ("acceptance", "confirmation sequence"),
    }.get(component_id, ())
    return (ConfirmationComponentState.PENDING if any(token in missing for token in tokens)
            or decision.setup_state in {SetupProgress.FORMING, SetupProgress.WATCHING, SetupProgress.TRIGGERED}
            else ConfirmationComponentState.UNAVAILABLE)


def _confirmation(decision: StandardDecision) -> list[ConfirmationComponent]:
    components = []
    for component_id, label in FAMILY_COMPONENTS.get(decision.setup_family, ()):
        state = _component_state(component_id, decision)
        evidence = None
        if component_id == "relative_volume" and state == ConfirmationComponentState.PASS:
            evidence = "Required relative-volume rule passed"
        elif state == ConfirmationComponentState.PASS:
            evidence = "Required setup sequence passed"
        components.append(ConfirmationComponent(id=component_id, label=label, state=state,
            required=True, evidence=evidence))
    return components


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _source_timeframes(types: list[str]) -> list[str]:
    result = []
    if any("OPENING_RANGE" in item or "SESSION" in item for item in types): result.append("SESSION")
    if any("PRIOR_DAY" in item for item in types): result.append("PRIOR_SESSION")
    if any("PREMARKET" in item for item in types): result.append("PREMARKET")
    if any("VWAP" in item for item in types): result.append("5m")
    return result or ["STRUCTURAL"]


def build_decision_intelligence(decision: StandardDecision, levels: list[PriceLevel], *,
                                data_state: str, market_session: str) -> DecisionIntelligence:
    action = _action(decision, data_state, market_session)
    components = _confirmation(decision)
    requirements = [
        {"id": item.id, "label": item.label, "state": item.state,
         "target_state": SetupProgress.CONFIRMED.value}
        for item in components if item.state != ConfirmationComponentState.PASS
    ]
    known_labels = {item.label.lower() for item in components}
    for label in decision.missing_confirmation:
        if label.lower() not in known_labels:
            requirements.append({"id": _slug(label), "label": label,
                "state": ConfirmationComponentState.PENDING,
                "target_state": SetupProgress.CONFIRMED.value})
    zone = (DecisionZone(lower_bound=decision.trigger, upper_bound=decision.trigger,
        role="SETUP_TRIGGER", source=f"{decision.setup_family.value} deterministic trigger",
        active=decision.setup_state not in {SetupProgress.NO_SETUP, SetupProgress.INVALIDATED, SetupProgress.TOO_LATE})
        if decision.trigger is not None else None)
    invalidation = None
    if decision.invalidation is not None:
        condition = ("Price trades below the mapped invalidation" if decision.direction == "LONG"
                     else "Price trades above the mapped invalidation")
        invalidation = InvalidationRule(type="PRICE_LEVEL", price=decision.invalidation,
            condition=condition, timeframe="1m observation",
            human_label=f"Invalidated if price trades {'below' if decision.direction == 'LONG' else 'above'} {decision.invalidation:.2f}.")
    enriched_levels = []
    for level in levels:
        categories = ["CONTEXT"]
        setup_specific = False
        for value, category in ((decision.trigger, "TRIGGER"), (decision.invalidation, "INVALIDATION"),
                                (decision.target_1, "TARGET_1"), (decision.target_2, "TARGET_2")):
            if value is not None and abs(level.price-value) < 0.0001:
                categories.append(category); setup_specific = True
        if level.position == "BELOW": categories.append("SUPPORT")
        elif level.position == "ABOVE": categories.append("RESISTANCE")
        enriched_levels.append(DecisionLevelMetadata(price=level.price,
            categories=list(dict.fromkeys(categories)), setup_specific=setup_specific,
            currently_interacting=bool(setup_specific and decision.trigger is not None
                and abs(level.price-decision.trigger) < 0.0001
                and decision.setup_state in {SetupProgress.WATCHING, SetupProgress.TRIGGERED, SetupProgress.CONFIRMED}),
            confluence_count=level.confluence, source_timeframes=_source_timeframes(level.types),
            freshness_context=data_state))
    return DecisionIntelligence(recommended_action=action,
        recommended_action_label=ACTION_LABELS[action], lifecycle_state=decision.setup_state.value,
        confirmation_components=components, next_state_requirements=requirements,
        decision_zone=zone, invalidation=invalidation,
        targets=[value for value in (decision.target_1, decision.target_2) if value is not None],
        structural_risk_reward=decision.structural_risk_reward, levels=enriched_levels)
