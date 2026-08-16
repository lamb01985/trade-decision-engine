from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.evidence_analytics import (EvidenceAnalyticsError, build_evidence_artifact,
                                    canonical_json, persist_artifact)

NOW = datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)


class Ledger:
    persistent = True

    def __init__(self, count=1):
        self._instances = []
        self._observations = {}
        self._outcomes = {}
        for index in range(count):
            self.add(f"set-{index}", terminal_r=1.0 if index % 2 == 0 else -1.0)

    def add(self, instance_id, *, terminal_r=1.0, outcome="TARGET_FIRST", complete=True,
            family="BREAKOUT_RETEST", direction="LONG", symbol="TEST",
            outcome_version="outcome-v1", rules_version="1.0.0", source_mode="REPLAY"):
        observation_id = f"obs-{instance_id}"
        self._instances.append({"setup_instance_id": instance_id,
            "identity_schema_version": "setup-instance-v1",
            "occurrence_policy_version": "occurrence-v1",
            "measurement_contract_version": "measurement-v1", "symbol": symbol,
            "setup_family": family, "direction": direction, "engine_version": "2.0.0",
            "setup_rules_version": rules_version, "parameter_version": "1.0.0"})
        ready = {"observation_id": observation_id, "setup_instance_id": instance_id,
            "event_type": "CANDIDATE", "recommended_action": "READY",
            "setup_family": family, "direction": direction, "minutes_since_regular_open": 30,
            "observation_contract_version": "setup-observation-v1",
            "feature_definition_version": "point-in-time-features-v1",
            "market_context": {"symbol_context": {"timeframes": {
                "5m": {"relative_volume": 1.5, "trend": "BULLISH"},
                "15m": {"trend": "BULLISH"}}}, "regime": {"trend": "UNAVAILABLE"}}}
        self._observations[instance_id] = [ready]
        self._outcomes[instance_id] = [{"outcome_assessment_id": f"out-{instance_id}",
            "setup_instance_id": instance_id, "reference_observation_id": observation_id,
            "supersedes_assessment_id": None, "primary_outcome": outcome,
            "terminal_r": terminal_r, "outcome_contract_version": outcome_version,
            "calculation_version": "minute-boundary-r-v1",
            "evaluation_horizon_id": "intraday-rth-calendar-v1",
            "evidence_granularity": "MASSIVE_ADJUSTED_MINUTE_AGGREGATES",
            "evidence_json": {"coverage": {"complete": complete}, "provenance": {
                "source_mode": source_mode, "replay_run_id": "rpl-test" if source_mode == "REPLAY" else None,
                "replay_contract_version": "historical-replay-v2",
                "session_calendar_version": "us-equities-session-calendar-v1",
                "replay_cache_version": "massive-minute-cache-v1"}}}]

    def instances(self, symbol=None):
        return list(self._instances)

    def observations(self, instance_id):
        return list(self._observations[instance_id])

    def outcome_assessments(self, instance_id):
        return list(self._outcomes[instance_id])


def aggregate(ledger, **kwargs):
    artifact = build_evidence_artifact(ledger, **kwargs)
    assert len(artifact["aggregates"]) == 1
    return artifact, artifact["aggregates"][0]


def test_one_instance_once_and_repeated_observations_do_not_inflate_n():
    ledger = Ledger()
    ledger._observations["set-0"].append({**ledger._observations["set-0"][0],
                                          "observation_id": "obs-later"})
    _, result = aggregate(ledger)
    assert result["denominator"]["n_total"] == 1
    assert result["denominator"]["n_evaluable"] == 1


def test_current_non_superseded_assessment_is_selected():
    ledger = Ledger()
    old = ledger._outcomes["set-0"][0]
    current = {**old, "outcome_assessment_id": "out-current",
               "supersedes_assessment_id": old["outcome_assessment_id"], "terminal_r": -0.5}
    ledger._outcomes["set-0"].append(current)
    _, result = aggregate(ledger)
    assert result["statistics"]["average_realized_r"] == -0.5
    assert result["sample_instance_ids"] == ["set-0"]


def test_incomplete_and_ambiguous_are_visible_and_excluded_from_realized_r():
    ledger = Ledger(0)
    ledger.add("incomplete", terminal_r=2.0, complete=False)
    ledger.add("ambiguous", terminal_r=3.0, outcome="AMBIGUOUS", complete=True)
    _, result = aggregate(ledger)
    assert result["denominator"] == {"n_total": 2, "n_evaluable": 0,
        "n_incomplete": 1, "n_ambiguous": 1,
        "excluded_reasons": {"AMBIGUOUS": 1, "INCOMPLETE_EVIDENCE": 1}}
    assert result["statistics"]["average_realized_r"] is None
    assert result["statistics"]["win_count"] == 0


def test_missing_slice_values_are_explicit():
    ledger = Ledger()
    ledger._observations["set-0"][0]["market_context"] = {}
    artifact = build_evidence_artifact(ledger, cohort_keys=(),
        slice_keys=("relative_volume", "volatility_atr", "trend_alignment", "market_regime"))
    result = artifact["aggregates"][0]
    assert set(result["slice"].values()) == {"UNAVAILABLE"}


def test_realized_r_statistics_and_confidence_interval():
    ledger = Ledger(0)
    for index, value in enumerate((-1.0, 0.0, 2.0)):
        ledger.add(f"r-{index}", terminal_r=value)
    _, result = aggregate(ledger)
    stats = result["statistics"]
    assert stats["win_count"] == stats["loss_count"] == stats["neutral_count"] == 1
    assert stats["average_realized_r"] == pytest.approx(1 / 3)
    assert stats["median_realized_r"] == 0
    assert stats["cumulative_realized_r"] == 1
    assert stats["minimum_realized_r"] == -1
    assert stats["maximum_realized_r"] == 2
    assert stats["standard_error_mean_r"] is not None
    assert len(stats["mean_r_95_confidence_interval"]) == 2


def test_empty_and_single_observation_cohorts_have_mathematically_valid_nulls():
    empty = build_evidence_artifact(Ledger(0))
    assert empty["aggregates"] == []
    _, single = aggregate(Ledger(1))
    assert single["statistics"]["standard_error_mean_r"] is None
    assert single["statistics"]["mean_r_95_confidence_interval"] is None


@pytest.mark.parametrize("count,label", [(19, "INSUFFICIENT"), (20, "PRELIMINARY"),
    (49, "PRELIMINARY"), (50, "MODERATE_SAMPLE"), (99, "MODERATE_SAMPLE"),
    (100, "SUBSTANTIAL_SAMPLE")])
def test_sample_size_thresholds(count, label):
    _, result = aggregate(Ledger(count))
    assert result["denominator"]["n_evaluable"] == count
    assert result["sample_label"] == label


def test_compatibility_separates_and_strict_mode_rejects_incompatible_versions():
    ledger = Ledger(0)
    ledger.add("v1", outcome_version="outcome-v1")
    ledger.add("v2", outcome_version="outcome-v2")
    artifact = build_evidence_artifact(ledger, cohort_keys=())
    assert len(artifact["aggregates"]) == 2
    with pytest.raises(EvidenceAnalyticsError, match="incompatible"):
        build_evidence_artifact(ledger, cohort_keys=(), reject_incompatible=True)


def test_mixed_symbol_setup_direction_cohorts_are_deterministic():
    ledger = Ledger(0)
    ledger.add("z", family="VWAP_RECLAIM", direction="SHORT", symbol="ZZZ")
    ledger.add("a", family="BREAKOUT_RETEST", direction="LONG", symbol="AAA")
    artifact = build_evidence_artifact(ledger,
        cohort_keys=("symbol", "setup_family", "direction"))
    assert [row["cohort"]["symbol"] for row in artifact["aggregates"]] == ["AAA", "ZZZ"]


def test_denominator_integrity_marks_duplicate_instance_cohort_invalid():
    ledger = Ledger()
    duplicate = {**ledger._outcomes["set-0"][0], "outcome_assessment_id": "fork"}
    ledger._outcomes["set-0"].append(duplicate)
    _, result = aggregate(ledger)
    assert result["valid"] is False
    assert result["statistics"] is None
    assert any("DUPLICATE_INSTANCE_COHORT" in reason for reason in result["invalid_reasons"])


def test_broken_supersession_cannot_silently_remove_denominator():
    ledger = Ledger()
    ledger._outcomes["set-0"][0]["supersedes_assessment_id"] = "missing"
    _, result = aggregate(ledger)
    assert result["valid"] is False
    assert result["denominator"]["n_total"] == 1
    assert result["statistics"] is None
    assert any("DANGLING_SUPERSESSION" in reason for reason in result["invalid_reasons"])


def test_identical_builds_have_identical_order_json_and_fingerprints(tmp_path):
    ledger = Ledger(3)
    first = build_evidence_artifact(ledger, cohort_keys=("direction", "symbol"),
                                    slice_keys=("time_of_day", "relative_volume"))
    ledger._instances.reverse()
    second = build_evidence_artifact(ledger, cohort_keys=("direction", "symbol"),
                                     slice_keys=("time_of_day", "relative_volume"))
    assert canonical_json(first) == canonical_json(second)
    assert first["generation_input_fingerprint"] == second["generation_input_fingerprint"]
    assert first["artifact_fingerprint"] == second["artifact_fingerprint"]
    path = persist_artifact(first, tmp_path)
    assert persist_artifact(second, tmp_path) == path
    assert json.loads(path.read_text())["artifact_fingerprint"] == first["artifact_fingerprint"]


def test_sqlite_persistence_and_read_only_api_interface(tmp_path):
    from types import SimpleNamespace

    from app.engines.setups import CandidateEvidence
    from app.main import app
    from app.models import SetupFamily, SetupProgress
    from app.outcome_evaluator import evaluate_ledger_loaded
    from app.parameters import DEFAULT_PARAMETERS
    from app.persistence import create_stores
    from tests.test_outcome_evaluator import full_session
    from tests.test_research_ledger import analysis

    stores = create_stores(f"sqlite:///{tmp_path / 'analytics.sqlite3'}", initialize=True)
    original = getattr(app.state, "persistence", None)
    try:
        current = analysis(NOW, SetupProgress.CONFIRMED)
        candidate = CandidateEvidence(SetupFamily.BREAKOUT_RETEST, "LONG",
            SetupProgress.CONFIRMED, 100.0, ("confirmed",), (),
            "MAPPED_RESISTANCE", {"price": 100.0, "level_types": ["PRIOR_DAY_HIGH"]})
        instance_id = stores.research.observe_candidates(current, [candidate], DEFAULT_PARAMETERS,
            source_mode="REPLAY", replay_run_id="rpl_phase4d")[0]
        evaluate_ledger_loaded(stores.research, {"TEST": full_session()})
        before_observations = list(stores.research.observations(instance_id))
        before_outcomes = list(stores.research.outcome_assessments(instance_id))
        app.state.persistence = SimpleNamespace(research=stores.research)
        response = TestClient(app).get("/api/v1/research/evidence",
            params={"cohort_by": "setup_family,direction", "slice_by": "time_of_day",
                    "source_mode": "REPLAY", "reject_incompatible": "true"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["aggregates"][0]["denominator"]["n_total"] == 1
        assert payload["aggregates"][0]["slice"]["time_of_day"] == "MIDDAY"
        assert list(stores.research.observations(instance_id)) == before_observations
        assert list(stores.research.outcome_assessments(instance_id)) == before_outcomes
    finally:
        app.state.persistence = original
        stores.signal.close()


def test_api_rejects_unsupported_dimensions_without_querying_trading_paths():
    from types import SimpleNamespace

    from app.main import app

    original = getattr(app.state, "persistence", None)
    app.state.persistence = SimpleNamespace(research=Ledger())
    try:
        response = TestClient(app).get("/api/v1/research/evidence",
            params={"cohort_by": "setup_family,not_a_dimension"})
        assert response.status_code == 422
    finally:
        app.state.persistence = original
