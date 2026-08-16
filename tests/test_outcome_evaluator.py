from datetime import datetime, timedelta, timezone

from app.models import Bar
from app.outcome_evaluator import evaluate_ready_observation

T0 = datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)


def ready(direction="LONG", **updates):
    row = {"observation_id": "obs-1", "setup_instance_id": "set-1",
        "event_type": "CANDIDATE", "recommended_action": "READY", "direction": direction,
        "decision_bar_timestamp": T0, "decision_reference_price": 100.0, "trigger": 100.0,
        "invalidation": 99.0 if direction == "LONG" else 101.0,
        "target_1": 101.0 if direction == "LONG" else 99.0,
        "target_2": 102.0 if direction == "LONG" else 98.0,
        "data_quality": {"source_mode": "REPLAY", "replay_run_id": "rpl_test"}}
    row.update(updates)
    return row


def bar(minutes, low=99.5, high=100.5, close=100.0):
    return Bar(timestamp=T0 + timedelta(minutes=minutes), open=100.0, high=high,
               low=low, close=close, volume=1000)


def full_session(overrides=None):
    overrides = overrides or {}
    return [overrides.get(i, bar(i)) for i in range(1, 300)]


def test_different_minute_ordering_and_frozen_reference():
    bars = full_session({1: bar(1, high=101.1), 2: bar(2, low=98.9)})
    result = evaluate_ready_observation(ready(), bars)
    assert result.assessment.primary_outcome == "TARGET_FIRST"
    assert result.assessment.ready_reference_price == 100.0
    assert result.assessment.theoretical_entry_price == 100.0


def test_same_minute_target_and_invalidation_is_ambiguous():
    result = evaluate_ready_observation(ready(), full_session({1: bar(1, low=98.9, high=101.1)}))
    assert result.assessment.primary_outcome == "AMBIGUOUS"
    assert result.assessment.ambiguity_reason == "TARGET_AND_INVALIDATION_TOUCHED_IN_SAME_MINUTE"


def test_missing_minute_is_not_forced_to_neither():
    bars = full_session()
    bars.pop(0)
    result = evaluate_ready_observation(ready(), bars)
    assert result.assessment.primary_outcome == "AMBIGUOUS"
    assert result.assessment.evidence["coverage"]["complete"] is False


def test_long_short_r_normalization_is_symmetric():
    long_result = evaluate_ready_observation(ready("LONG"), full_session({1: bar(1, high=101.2)}))
    short_result = evaluate_ready_observation(ready("SHORT"), full_session({1: bar(1, low=98.8)}))
    assert long_result.assessment.target_1_r == short_result.assessment.target_1_r == 1.0
    assert long_result.assessment.mfe_r == short_result.assessment.mfe_r


def test_incomplete_frozen_plan_is_not_evaluable():
    result = evaluate_ready_observation(ready(target_1=None), full_session())
    assert result.status == "NOT_EVALUABLE"
    assert result.assessment is None


def test_ready_on_final_session_minute_has_complete_zero_minute_horizon():
    final_ready = ready(decision_bar_timestamp=datetime(2026, 8, 12, 19, 59,
                                                        tzinfo=timezone.utc))
    result = evaluate_ready_observation(final_ready, [])
    assert result.assessment.primary_outcome == "NEITHER"
    assert result.assessment.evidence["coverage"]["expected_minutes"] == 0
    assert result.assessment.end_of_session_r == 0.0


def test_provenance_carries_replay_contract_and_source_mode():
    result = evaluate_ready_observation(ready(), full_session())
    provenance = result.assessment.evidence["provenance"]
    assert provenance["source_mode"] == "REPLAY"
    assert provenance["replay_run_id"] == "rpl_test"
    assert provenance["replay_contract_version"].startswith("historical-replay-v")


def test_batch_append_is_idempotent_and_does_not_mutate_ready_observation(tmp_path):
    from app.engines.setups import CandidateEvidence
    from app.models import SetupFamily, SetupProgress
    from app.outcome_evaluator import evaluate_ledger_loaded
    from app.parameters import DEFAULT_PARAMETERS
    from app.persistence import create_stores
    from tests.test_research_ledger import analysis

    stores = create_stores(f"sqlite:///{tmp_path / 'outcomes.sqlite3'}", initialize=True)
    try:
        current = analysis(T0, SetupProgress.CONFIRMED)
        candidate = CandidateEvidence(SetupFamily.BREAKOUT_RETEST, "LONG",
            SetupProgress.CONFIRMED, 100.0, ("confirmed",), (),
            "MAPPED_RESISTANCE", {"price": 100.0, "level_types": ["PRIOR_DAY_HIGH"]})
        instance_id = stores.research.observe_candidates(current, [candidate], DEFAULT_PARAMETERS,
            source_mode="REPLAY", replay_run_id="rpl_test")[0]
        original = dict(stores.research.observations(instance_id)[0])
        first = evaluate_ledger_loaded(stores.research, {"TEST": full_session()})
        second = evaluate_ledger_loaded(stores.research, {"TEST": full_session()})
        assert first.inserted == 1 and first.duplicates == 0
        assert second.inserted == 0 and second.duplicates == 1
        assert len(stores.research.outcome_assessments(instance_id)) == 1
        assert dict(stores.research.observations(instance_id)[0]) == original
    finally:
        stores.signal.close()
