from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.engines.setups import CandidateEvidence
from app.models import (ActionNow, DecisionQuality, InstrumentAnalysis, SetupDecision,
    SetupFamily, SetupProgress, SetupScore, StandardDecision, SymbolAnalysis,
    TimeframeAnalysis, TradePlan, Trend)
from app.parameters import DEFAULT_PARAMETERS
from app.persistence import OutcomeAssessment, ScanCycleCoverage, ScanSymbolCoverage, create_stores
from app.research_identity import anchor_key, setup_instance_id
from app.versioning import ENGINE_VERSION, PARAMETER_VERSION, SETUP_RULES_VERSION


T0 = datetime(2026, 8, 14, 15, 4, tzinfo=timezone.utc)


def candidate(state=SetupProgress.FORMING, anchor=None):
    return CandidateEvidence(SetupFamily.BREAKOUT_RETEST, "LONG", state, 100.0,
        ("existing detector rule",), ("5m acceptance/retest",), "MAPPED_RESISTANCE",
        anchor or {"price": 100.0, "level_types": ["PRIOR_DAY_HIGH"]})


def analysis(at=T0, decision_state=SetupProgress.FORMING):
    frame = TimeframeAnalysis(timeframe="5m", price=99.8, vwap=99.0, ema_9=99.5,
        ema_21=99.0, vwap_state="ABOVE", ema_alignment="BULLISH",
        distance_from_vwap=.8, distance_from_vwap_pct=.8, trend=Trend.BULLISH,
        volume=1000, relative_volume=1.2)
    instrument = InstrumentAnalysis(symbol="TEST", available=True, as_of=at,
        timeframes={name: frame.model_copy(update={"timeframe": name})
                    for name in ("1m", "5m", "15m")}, atr_5m=1.0)
    decision = StandardDecision(symbol="TEST", timestamp=at, market_price=99.8,
        direction="LONG", setup_family=SetupFamily.BREAKOUT_RETEST,
        setup_state=decision_state, quality=DecisionQuality.B,
        action_now=ActionNow.PREPARE_LONG, trigger=100.0, invalidation=99.0,
        target_1=101.0, target_2=102.0, structural_risk_reward=2.0,
        vwap_state="ABOVE", structure_5m=Trend.BULLISH,
        structure_15m=Trend.BULLISH, reasoning=["existing detector rule"],
        missing_confirmation=["5m acceptance/retest"], do_not_trade_if=[],
        what_changes_my_mind=[], state_changed_at=at)
    return SymbolAnalysis(instrument=instrument, levels=[],
        setup=SetupDecision(state="WATCH_LONG", explanation=[]),
        setup_score=SetupScore(score=70, factors=[]),
        trade_plan=TradePlan(status="WATCH", explanation="test"),
        what_would_change_state=[], thesis="test", current_structure="test",
        confirmation="pending", permission_to_trade=False, decision=decision,
        provider_timestamp=at, analysis_timestamp=at + timedelta(seconds=2),
        decision_bar_timestamp=at, data_age_seconds=2, data_status="LIVE",
        data_state="LIVE", market_session="REGULAR", current_price=99.8,
        price_source="test")


@pytest.fixture
def ledger(tmp_path):
    stores = create_stores(f"sqlite:///{tmp_path / 'ledger.sqlite3'}", initialize=True)
    yield stores.research
    stores.signal.close()


def test_setup_instance_identity_is_deterministic_versioned_and_uses_full_digest():
    values = dict(symbol="test", session_date=date(2026, 8, 14),
        setup_family="BREAKOUT_RETEST", direction="LONG",
        family_anchor_type="MAPPED_RESISTANCE",
        family_anchor={"level_types": ["PRIOR_DAY_HIGH"], "price": 100.0},
        first_qualifying_bar_at=T0, engine_version=ENGINE_VERSION,
        setup_rules_version=SETUP_RULES_VERSION, parameter_version=PARAMETER_VERSION)
    first = setup_instance_id(**values)
    reordered = setup_instance_id(**{**values,
        "family_anchor": {"price": 100, "level_types": ["PRIOR_DAY_HIGH"]}})
    changed = setup_instance_id(**{**values, "parameter_version": "parameters-v-next"})
    assert first == reordered and first.startswith("set_") and len(first) == 68
    assert first != changed
    assert anchor_key("MAPPED_RESISTANCE", values["family_anchor"]) == anchor_key(
        "MAPPED_RESISTANCE", {"price": 100, "level_types": ["PRIOR_DAY_HIGH"]})


def test_repeated_polls_are_one_instance_and_only_meaningful_states_append(ledger):
    first = ledger.observe_candidates(analysis(), [candidate()], DEFAULT_PARAMETERS, cycle_id=str(uuid4()))
    second = ledger.observe_candidates(analysis(), [candidate()], DEFAULT_PARAMETERS, cycle_id=str(uuid4()))
    assert first == second
    assert len(ledger.instances("TEST")) == 1
    assert len(ledger.observations(first[0])) == 1
    later = T0 + timedelta(minutes=5)
    ledger.observe_candidates(analysis(later, SetupProgress.TRIGGERED),
        [candidate(SetupProgress.TRIGGERED)], DEFAULT_PARAMETERS)
    observations = ledger.observations(first[0])
    assert [row["setup_state"] for row in observations] == ["FORMING", "TRIGGERED"]


def test_opening_range_raw_anchor_survives_moving_merged_trigger_and_terminal_reforms(ledger):
    def opening_candidate(trigger, at, state=SetupProgress.FORMING):
        return CandidateEvidence(SetupFamily.OPENING_RANGE_BREAKOUT, "LONG", state,
            trigger, ("existing opening-range rule",), ("5m acceptance/retest",),
            "OPENING_RANGE_HIGH", {"price": 99.75, "role": "OPENING_RANGE_HIGH",
                                   "opening_range_minutes": 30}, at)
    def selected(at, trigger, state):
        base = analysis(at, state)
        decision = base.decision.model_copy(update={
            "setup_family": SetupFamily.OPENING_RANGE_BREAKOUT,
            "direction": "LONG", "trigger": trigger, "setup_state": state})
        return base.model_copy(update={"decision": decision})

    first = ledger.observe_candidates(selected(T0, 100.0, SetupProgress.FORMING),
        [opening_candidate(100.0, T0)], DEFAULT_PARAMETERS)[0]
    later = T0 + timedelta(minutes=5)
    same = ledger.observe_candidates(selected(later, 100.2, SetupProgress.WATCHING),
        [opening_candidate(100.2, later, SetupProgress.WATCHING)], DEFAULT_PARAMETERS)[0]
    assert same == first
    assert len(ledger.instances("TEST")) == 1

    terminal_at = later + timedelta(minutes=5)
    ledger.observe_candidates(selected(terminal_at, 100.2, SetupProgress.TOO_LATE),
        [opening_candidate(100.2, terminal_at, SetupProgress.WATCHING)], DEFAULT_PARAMETERS)
    reformed_at = terminal_at + timedelta(minutes=5)
    reformed = ledger.observe_candidates(selected(reformed_at, 100.3, SetupProgress.FORMING),
        [opening_candidate(100.3, reformed_at)], DEFAULT_PARAMETERS)[0]
    assert reformed != first
    assert len(ledger.instances("TEST")) == 2
    assert ledger.observations(first)[-1]["closure_reason"] == "EXPIRED"


def test_disappearance_grace_reappearance_and_terminal_closure(ledger):
    instance_id = ledger.observe_candidates(analysis(), [candidate()], DEFAULT_PARAMETERS)[0]
    session = date(2026, 8, 14)
    ledger.reconcile_symbol("TEST", session_date=session,
        decision_bar_timestamp=T0 + timedelta(minutes=5), observed_instance_ids=[])
    ledger.reconcile_symbol("TEST", session_date=session,
        decision_bar_timestamp=T0 + timedelta(minutes=5), observed_instance_ids=[])
    assert [row["event_type"] for row in ledger.observations(instance_id)] == [
        "CANDIDATE", "ABSENCE_PENDING"]
    ledger.observe_candidates(analysis(T0 + timedelta(minutes=10)), [candidate()], DEFAULT_PARAMETERS)
    ledger.reconcile_symbol("TEST", session_date=session,
        decision_bar_timestamp=T0 + timedelta(minutes=15), observed_instance_ids=[])
    ledger.reconcile_symbol("TEST", session_date=session,
        decision_bar_timestamp=T0 + timedelta(minutes=20), observed_instance_ids=[])
    rows = ledger.observations(instance_id)
    assert rows[-1]["event_type"] == "CLOSURE"
    assert rows[-1]["closure_reason"] == "CONDITION_NO_LONGER_PRESENT"


def test_data_gap_freezes_absence_and_is_session_scoped(ledger):
    instance_id = ledger.observe_candidates(analysis(), [candidate()], DEFAULT_PARAMETERS)[0]
    ledger.record_data_unavailable("TEST", observed_at=T0 + timedelta(minutes=5),
        session_date=date(2026, 8, 14), data_state="DATA_STALE")
    ledger.record_data_unavailable("TEST", observed_at=T0 + timedelta(minutes=10),
        session_date=date(2026, 8, 15), data_state="DATA_STALE")
    rows = ledger.observations(instance_id)
    assert [row["event_type"] for row in rows] == ["CANDIDATE", "DATA_UNAVAILABLE"]
    assert rows[-1]["closure_reason"] is None


def test_scan_coverage_distinguishes_no_setup_not_observed_and_partial(ledger):
    cycle_id = str(uuid4())
    coverage = ScanCycleCoverage(cycle_id=cycle_id, scanner_scope="hosted",
        run_id="task-a", started_at=T0, completed_at=T0 + timedelta(seconds=4),
        coverage_status="PARTIAL", market_session="REGULAR", data_status="PARTIAL",
        expected_symbols=("TEST", "MISS"), scanner_ready_at_start=True,
        scanner_ready_at_end=False, symbols=(
            ScanSymbolCoverage("TEST", "SUCCESS", "NO_SETUP", T0, T0, T0, 2,
                "LIVE", "REGULAR"),
            ScanSymbolCoverage("MISS", "DATA_STALE", "NOT_OBSERVED", None, None,
                T0 - timedelta(minutes=20), 1200, "STALE", "REGULAR",
                error_code="STALE_DATA")))
    ledger.record_scan_cycle(coverage)
    stored = ledger.scan_cycle(cycle_id)
    assert stored["succeeded_count"] == 1 and stored["failed_count"] == 1
    with pytest.raises(ValueError, match="exactly one"):
        ledger.record_scan_cycle(ScanCycleCoverage(**{
            **coverage.__dict__, "cycle_id": str(uuid4()), "symbols": coverage.symbols[:1]}))


def test_no_lookahead_rejected_and_version_metadata_is_immutable(ledger):
    invalid = analysis().model_copy(update={"instrument": analysis().instrument.model_copy(
        update={"as_of": T0 + timedelta(minutes=1)})})
    with pytest.raises(ValueError, match="newer"):
        ledger.observe_candidates(invalid, [candidate()], DEFAULT_PARAMETERS)
    instance_id = ledger.observe_candidates(analysis(), [candidate()], DEFAULT_PARAMETERS)[0]
    instance = ledger.instances("TEST")[0]
    observation = ledger.observations(instance_id)[0]
    assert (instance["engine_version"], instance["setup_rules_version"],
        instance["parameter_version"]) == (ENGINE_VERSION, SETUP_RULES_VERSION, PARAMETER_VERSION)
    assert observation["feature_source_at"] <= observation["decision_bar_timestamp"]
    assert observation["research_status"] == "PRODUCTION"
    assert observation["actionable_allowed"] is True


def test_ambiguous_outcome_is_append_only_and_does_not_modify_observation(ledger):
    instance_id = ledger.observe_candidates(analysis(), [candidate()], DEFAULT_PARAMETERS)[0]
    original = dict(ledger.observations(instance_id)[0])
    assessment = OutcomeAssessment(outcome_assessment_id=str(uuid4()),
        setup_instance_id=instance_id, reference_observation_id=original["observation_id"],
        primary_outcome="AMBIGUOUS", outcome_contract_version="outcome-v1",
        calculation_version="not-evaluated-v1", evaluation_horizon_id="intraday-rth-v1",
        evidence_granularity="MINUTE_BAR", evidence_start_at=T0,
        evidence_cutoff_at=T0 + timedelta(minutes=5), computed_at=T0 + timedelta(minutes=6),
        ambiguity_reason="target and invalidation both touched in one minute bar",
        evidence={"ordering": "UNRESOLVED"})
    assert ledger.append_outcome(assessment) is True
    assert ledger.append_outcome(assessment) is False
    assert ledger.outcome_assessments(instance_id)[0]["primary_outcome"] == "AMBIGUOUS"
    assert dict(ledger.observations(instance_id)[0]) == original


def test_restart_reopens_same_sqlite_ledger(tmp_path):
    url = f"sqlite:///{tmp_path / 'restart.sqlite3'}"
    first = create_stores(url, initialize=True)
    instance_id = first.research.observe_candidates(
        analysis(), [candidate()], DEFAULT_PARAMETERS)[0]
    first.signal.close()
    second = create_stores(url)
    try:
        assert second.research.instances("TEST")[0]["setup_instance_id"] == instance_id
        assert len(second.research.observations(instance_id)) == 1
    finally:
        second.signal.close()


def test_service_captures_detector_denominator_before_selection(monkeypatch):
    import asyncio
    from app.freshness import DataStatus, MarketSession
    from app.service import AnalysisService
    from tests.test_service_v2 import FakeProvider

    class SpyLedger:
        def __init__(self):
            self.candidates = None
        def observe_candidates(self, current, candidates, parameters, *, cycle_id=None):
            self.candidates = tuple(candidates)
            return [f"instance-{index}" for index, _ in enumerate(candidates)]
        def reconcile_symbol(self, *args, **kwargs):
            return None
        def record_data_unavailable(self, *args, **kwargs):
            raise AssertionError("fresh regular data must not be classified unavailable")

    spy = SpyLedger()
    monkeypatch.setattr("app.service.classify_data",
        lambda provider_timestamp, now=None: (DataStatus.LIVE, MarketSession.REGULAR, 0))
    result = asyncio.run(AnalysisService(FakeProvider(), research_ledger=spy).analyze_symbol(
        "NVDA", include_cross_market=False, force=True, persist_actionable_only=True))
    assert spy.candidates is not None
    assert len(spy.candidates) > 0
    assert len(spy.candidates) == len(result.research_candidate_instance_ids)
    assert all(item.family != SetupFamily.NONE for item in spy.candidates)
