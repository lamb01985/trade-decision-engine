from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.excursion_analytics import (MEASUREMENT_CONTRACT_VERSION, build_excursion_artifact,
                                     canonical_json, persist_artifact)
from app.models import Bar

UTC = timezone.utc
START = datetime(2026, 8, 12, 15, 0, tzinfo=UTC)  # 10:00 America/Chicago


class Ledger:
    persistent = True

    def __init__(self):
        self._instances = []
        self._observations = {}
        self._outcomes = {}

    def add(self, instance_id="one", *, direction="LONG", trigger=100.0, invalidation=99.0,
            family="BREAKOUT_RETEST", symbol="TEST", source_mode="REPLAY", rules="1.0.0",
            outcome="TARGET_FIRST", ready_at=START):
        self._instances.append({"setup_instance_id": instance_id, "symbol": symbol,
            "setup_family": family, "direction": direction, "identity_schema_version": "setup-instance-v1",
            "occurrence_policy_version": "occurrence-v1", "engine_version": "2.0.0",
            "setup_rules_version": rules, "parameter_version": "1.0.0"})
        observation = {"observation_id": f"obs-{instance_id}", "setup_instance_id": instance_id,
            "event_type": "CANDIDATE", "recommended_action": "READY", "decision_bar_timestamp": ready_at,
            "setup_family": family, "direction": direction, "trigger": trigger, "invalidation": invalidation,
            "target_1": 101.0 if direction == "LONG" else 99.0, "initial_risk_reward": 2.0,
            "nearest_resistance": 102.0 if direction == "LONG" else 101.0,
            "nearest_support": 99.0 if direction == "LONG" else 98.0,
            "minutes_since_regular_open": 30, "observation_contract_version": "setup-observation-v1",
            "feature_definition_version": "point-in-time-features-v1", "context_contract_version": "context-v1",
            "regime_contract_version": "regime-v1", "data_quality": {"source_mode": source_mode,
                "replay_run_id": "replay-1" if source_mode == "REPLAY" else None,
                "market_data_version": "bars-v1"},
            "market_context": {"symbol_context": {"atr_5m": 1.0, "high_of_day": 103.0,
                "low_of_day": 97.0, "prior_day_high": 104.0, "prior_day_low": 96.0,
                "opening_range_high": 102.5, "opening_range_low": 97.5,
                "timeframes": {"5m": {"vwap": 99.5, "relative_volume": 1.5, "trend": "BULLISH"},
                               "15m": {"trend": "BULLISH"}}}, "regime": {"trend": "TRENDING"}}}
        self._observations[instance_id] = [observation]
        self._outcomes[instance_id] = [{"outcome_assessment_id": f"out-{instance_id}",
            "reference_observation_id": observation["observation_id"], "primary_outcome": outcome}]

    def instances(self, symbol=None): return list(self._instances)
    def observations(self, instance_id): return list(self._observations[instance_id])
    def outcome_assessments(self, instance_id): return list(self._outcomes[instance_id])


def bars(prices, start=START):
    return [Bar(timestamp=start + timedelta(minutes=index), open=close, high=high, low=low,
                close=close, volume=1000) for index, (high, low, close) in enumerate(prices, 1)]


def measurement(ledger, evidence, horizon=5):
    return build_excursion_artifact(ledger, {"TEST": evidence}, horizons=(horizon,),
                                     cohort_keys=())["measurements"][0]


def test_long_mfe_mae_r_normalization_timing_and_end_return():
    ledger = Ledger(); ledger.add()
    row = measurement(ledger, bars([(100.5, 99.8, 100.2), (102.0, 99.7, 101.5),
                                    (101.2, 98.5, 99.0), (100.2, 99.2, 100), (100, 100, 100)]))
    assert row["mfe"]["price"] == 2 and row["mfe"]["r"] == 2
    assert row["mae"]["price"] == 1.5 and row["mae"]["r"] == 1.5
    assert row["mfe"]["time_seconds"] == 120 and row["mae"]["time_seconds"] == 180
    assert row["end_return"]["r"] == 0


def test_short_mfe_and_mae_are_positive_magnitudes():
    ledger = Ledger(); ledger.add(direction="SHORT", trigger=100, invalidation=101)
    row = measurement(ledger, bars([(100.2, 99.5, 99.8), (101.4, 97.5, 98),
                                    (100, 99, 99), (100, 99, 99), (100, 99, 99)]))
    assert row["mfe"]["price"] == 2.5 and row["mfe"]["r"] == 2.5
    assert row["mae"]["price"] == pytest.approx(1.4) and row["mae"]["r"] == pytest.approx(1.4)


@pytest.mark.parametrize("invalidation,reason", [(None, "MISSING_REFERENCE_OR_INVALIDATION"),
                                                   (100, "NON_POSITIVE_OR_NON_FINITE_RISK")])
def test_missing_or_invalid_risk_is_null_not_invented(invalidation, reason):
    ledger = Ledger(); ledger.add(invalidation=invalidation)
    row = measurement(ledger, bars([(101, 99.5, 100)] * 5))
    assert row["risk_1r"] is None and row["risk_unavailable_reason"] == reason
    assert row["mfe"]["r"] is None and row["mae"]["r"] is None
    assert row["thresholds"]["1R"]["reached"] is None


def test_duplicate_ready_observations_are_one_sample_using_first_chronologically():
    ledger = Ledger(); ledger.add()
    later = {**ledger._observations["one"][0], "observation_id": "later",
             "decision_bar_timestamp": START + timedelta(minutes=2), "trigger": 200}
    ledger._observations["one"].append(later)
    artifact = build_excursion_artifact(ledger, {"TEST": bars([(101, 99.5, 100)] * 5)},
                                        horizons=(5,), cohort_keys=())
    assert len(artifact["measurements"]) == 1
    assert artifact["measurements"][0]["reference_observation_id"] == "obs-one"


def test_multiple_horizons_are_independent_and_extensible():
    ledger = Ledger(); ledger.add()
    evidence = bars([(100.1, 99.9, 100)] * 5 + [(102, 99.9, 101)] * 10)
    rows = build_excursion_artifact(ledger, {"TEST": evidence}, horizons=(5, 15), cohort_keys=())["measurements"]
    assert [row["horizon_id"] for row in rows] == ["15M", "5M"]
    assert rows[0]["mfe"]["price"] == 2 and rows[1]["mfe"]["price"] == pytest.approx(.1)


def test_missing_forward_bar_is_incomplete_not_failure_or_zero():
    ledger = Ledger(); ledger.add()
    evidence = bars([(101, 99.5, 100)] * 4)
    row = measurement(ledger, evidence)
    assert row["coverage"]["complete"] is False
    assert "MISSING_FORWARD_BARS" in row["coverage"]["incomplete_reasons"]
    aggregate = build_excursion_artifact(ledger, {"TEST": evidence}, horizons=(5,), cohort_keys=())["aggregates"][0]
    assert aggregate["denominator"]["n_total"] == 1 and aggregate["denominator"]["n_evaluable"] == 0


def test_session_boundary_truncation_is_explicit_and_never_overnight():
    near_close = datetime(2026, 8, 12, 19, 58, tzinfo=UTC)  # 15:58 New York
    ledger = Ledger(); ledger.add(ready_at=near_close)
    evidence = bars([(101, 99, 100), (120, 80, 100)], start=near_close)
    row = measurement(ledger, evidence, horizon=5)
    assert row["coverage"]["session_boundary_truncated"] is True
    assert row["coverage"]["expected_minutes"] == 1
    assert row["mfe"]["price"] == 1


def test_threshold_crossing_and_time_to_threshold():
    ledger = Ledger(); ledger.add()
    row = measurement(ledger, bars([(100.2, 99.9, 100), (100.6, 99.8, 100.5),
                                    (101.1, 99.8, 101), (101, 100, 100), (100, 100, 100)]))
    assert row["thresholds"]["0.5R"]["reached"] is True
    assert row["thresholds"]["0.5R"]["time_to_seconds"] == 120
    assert row["thresholds"]["1R"]["time_to_seconds"] == 180
    assert row["thresholds"]["1.5R"]["reached"] is False
    assert row["thresholds"]["1R"]["invalidation_before_threshold"] is False


def test_same_bar_threshold_and_invalidation_has_no_fabricated_order():
    ledger = Ledger(); ledger.add()
    row = measurement(ledger, bars([(101.2, 98.8, 100), (100, 100, 100),
                                    (100, 100, 100), (100, 100, 100), (100, 100, 100)]))
    assert row["ambiguous"] is True
    assert row["thresholds"]["1R"]["reached"] is None
    assert row["thresholds"]["1R"]["invalidation_before_threshold"] is None
    assert row["thresholds"]["1R"]["ambiguity_reason"] == "THRESHOLD_AND_INVALIDATION_SAME_MINUTE"


def test_mfe_before_invalidation_excludes_ambiguous_invalidation_bar():
    ledger = Ledger(); ledger.add()
    row = measurement(ledger, bars([(100.5, 99.8, 100.2), (102, 98.8, 100),
                                    (100, 100, 100), (100, 100, 100), (100, 100, 100)]))
    assert row["mfe_before_invalidation"]["price"] == .5
    assert row["thresholds"]["3R"]["invalidation_before_threshold"] is True


def test_end_of_session_return_is_only_exposed_on_session_horizon():
    ledger = Ledger(); ledger.add()
    evidence = bars([(100.2, 99.9, 100)] * 5)
    intraday = measurement(ledger, evidence)
    assert intraday["end_of_session_return"] is None


def test_mae_before_favorable_movement_and_same_bar_ambiguity():
    ledger = Ledger(); ledger.add()
    row = measurement(ledger, bars([(100.2, 99.7, 100), (100.6, 99.4, 100.5),
                                    (100, 100, 100), (100, 100, 100), (100, 100, 100)]))
    assert row["mae_before_meaningful_favorable"]["r"] == pytest.approx(.3)
    assert "MAE_AND_MEANINGFUL_FAVORABLE_SAME_MINUTE" in row["ambiguity_reasons"]


def test_outcome_conditioned_aggregation_percentiles_and_threshold_rates():
    ledger = Ledger(); ledger.add("a", outcome="TARGET_FIRST"); ledger.add("b", outcome="TARGET_FIRST")
    evidence = bars([(100.5, 99.8, 100)] * 5)
    artifact = build_excursion_artifact(ledger, {"TEST": evidence}, horizons=(5,),
        cohort_keys=("outcome_state",))
    aggregate = artifact["aggregates"][0]
    assert aggregate["cohort"]["outcome_state"] == "TARGET_FIRST"
    assert aggregate["metrics"]["mfe_r"]["median"] == .5
    assert aggregate["metrics"]["mfe_r"]["p90"] == .5
    assert aggregate["thresholds"]["0.5R"]["probability_reached"] == 1


def test_cohort_slices_room_to_run_and_sample_labels():
    ledger = Ledger()
    for index in range(20): ledger.add(str(index), direction="LONG" if index < 10 else "SHORT")
    evidence = bars([(101, 99.5, 100)] * 5)
    artifact = build_excursion_artifact(ledger, {"TEST": evidence}, horizons=(5,),
        cohort_keys=("direction",), slice_keys=("structural_space_atr",))
    assert {row["cohort"]["direction"] for row in artifact["aggregates"]} == {"LONG", "SHORT"}
    assert all(row["sample_label"] == "INSUFFICIENT" for row in artifact["aggregates"])
    long = next(row for row in artifact["measurements"] if row["direction"] == "LONG")
    assert long["opportunity"]["structural_space_atr"] == 2
    assert long["slices"]["structural_space_atr"] == "GE_2_ATR"


def test_live_replay_and_version_compatibility_never_pool():
    ledger = Ledger(); ledger.add("live", source_mode="LIVE"); ledger.add("replay", source_mode="REPLAY")
    evidence = bars([(101, 99.5, 100)] * 5)
    artifact = build_excursion_artifact(ledger, {"TEST": evidence}, horizons=(5,), cohort_keys=())
    assert len(artifact["aggregates"]) == 2
    with pytest.raises(Exception, match="incompatible"):
        build_excursion_artifact(ledger, {"TEST": evidence}, horizons=(5,), cohort_keys=(),
                                 reject_incompatible=True)


def test_deterministic_rebuild_persistence_and_contract_fingerprint(tmp_path, monkeypatch):
    ledger = Ledger(); ledger.add()
    evidence = bars([(101, 99.5, 100)] * 5)
    first = build_excursion_artifact(ledger, {"TEST": evidence}, horizons=(5,), cohort_keys=())
    ledger._instances.reverse()
    second = build_excursion_artifact(ledger, {"TEST": list(reversed(evidence))}, horizons=(5,), cohort_keys=())
    assert canonical_json(first) == canonical_json(second)
    assert persist_artifact(first, tmp_path) == persist_artifact(second, tmp_path)
    import app.excursion_analytics as module
    monkeypatch.setattr(module, "MEASUREMENT_CONTRACT_VERSION", MEASUREMENT_CONTRACT_VERSION + "-changed")
    changed = module.build_excursion_artifact(ledger, {"TEST": evidence}, horizons=(5,), cohort_keys=())
    assert changed["artifact_fingerprint"] != first["artifact_fingerprint"]


def test_builder_does_not_mutate_ledger_or_bars():
    ledger = Ledger(); ledger.add(); evidence = bars([(101, 99.5, 100)] * 5)
    before_ledger, before_bars = deepcopy(ledger.__dict__), deepcopy(evidence)
    build_excursion_artifact(ledger, {"TEST": evidence}, horizons=(5,), cohort_keys=())
    assert ledger.__dict__ == before_ledger and evidence == before_bars


def test_read_only_api_requires_explicit_evidence_and_returns_artifact():
    from types import SimpleNamespace
    from app.main import app
    ledger = Ledger(); ledger.add(); evidence = bars([(101, 99.5, 100)] * 5)
    old_persistence = getattr(app.state, "persistence", None)
    old_bars = getattr(app.state, "excursion_bars_by_symbol", None)
    try:
        app.state.persistence = SimpleNamespace(research=ledger)
        if hasattr(app.state, "excursion_bars_by_symbol"):
            del app.state.excursion_bars_by_symbol
        assert TestClient(app).get("/api/v1/research/excursions?horizons=5").status_code == 503
        app.state.excursion_bars_by_symbol = {"TEST": evidence}
        response = TestClient(app).get(
            "/api/v1/research/excursions?horizons=5&cohort_by=direction&source_mode=REPLAY")
        assert response.status_code == 200
        assert response.json()["aggregates"][0]["denominator"]["n_total"] == 1
    finally:
        app.state.persistence = old_persistence
        if old_bars is None and hasattr(app.state, "excursion_bars_by_symbol"):
            del app.state.excursion_bars_by_symbol
        elif old_bars is not None:
            app.state.excursion_bars_by_symbol = old_bars
