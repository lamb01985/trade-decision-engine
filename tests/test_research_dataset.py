from __future__ import annotations

import json
from datetime import date

import pytest

from app.research_dataset import (DATASET_CONTRACT_VERSION, SOURCE_MODE,
    CompatibilityError, ResearchRunConfig, UnitResult, chronological_partitions,
    compatibility, run_loaded)
from tests.test_v2 import market_bars


SESSIONS = tuple(date(2026, 8, day) for day in (10, 11, 12, 13, 14))


def config(tmp_path, symbols=("NVDA", "SPY"), sessions=SESSIONS, sha="a" * 40):
    return ResearchRunConfig(symbols, sessions, tmp_path / "run", sha)


def fake_processor(calls, *, incomplete=None, failed=None):
    def process(cfg, symbol, session, bars):
        calls.append((symbol, session))
        if (symbol, session) == failed:
            raise ValueError("fixed correctness failure")
        status = "INCOMPLETE" if (symbol, session) == incomplete else "COMPLETE"
        record = {"setup_instance_id": f"{symbol}-{session}", "horizon_id": "END_OF_SESSION",
            "symbol": symbol, "setup_family": "BREAKOUT_RETEST", "direction": "LONG",
            "reference_at": f"{session}T15:00:00+00:00", "source_mode": "REPLAY",
            "coverage": {"complete": status == "COMPLETE"}, "outcome": {"primary_outcome": "NEITHER", "terminal_r": .2},
            "mfe": {"price": 1.0, "r": 1.0, "time_seconds": 60},
            "mae": {"price": .2, "r": .2, "time_seconds": 120},
            "opportunity": {"structural_space_atr": 1.5}, "slices": {"time_of_day": "MIDDAY"},
            "thresholds": {key: {"reached": key in {"0.25R", "0.5R", "1R"}}
                           for key in ("0.25R", "0.5R", "1R", "1.5R", "2R", "3R")},
            "provenance": {"research_run_id": cfg.research_run_id}}
        return UnitResult(symbol, session, status,
            failure_class="DATA_GAP" if status != "COMPLETE" else None,
            coverage={"setup_instances": 1, "ready_instances": 1, "outcome_assessments": 1,
                "excursion_assessments": 1}, records=[record],
            artifact_fingerprints={"excursion": "x"}, performance={"end_to_end_seconds": .1})
    return process


def all_bars(cfg):
    return {(symbol, session): [] for symbol in cfg.symbols for session in cfg.sessions}


def test_manifest_dataset_coverage_and_partitions_are_canonical(tmp_path):
    cfg = config(tmp_path)
    output = run_loaded(cfg, all_bars(cfg), fake_processor([]))
    assert output["manifest"]["coverage"]["sessions_completed"] == 10
    assert output["manifest"]["coverage"]["setup_instances"] == 10
    assert output["dataset"]["contract_version"] == DATASET_CONTRACT_VERSION
    assert output["dataset"]["partitions"]["assignments"]["2026-08-14"] == "HOLDOUT"
    assert output["baseline"]["scope"] == "DESCRIPTIVE_ONLY_NO_OPTIMIZATION"
    assert json.loads((cfg.output_directory / "manifest.json").read_text())["semantic_fingerprint"]


def test_resumption_idempotency_and_interrupted_recovery(tmp_path):
    cfg = config(tmp_path, symbols=("NVDA",), sessions=SESSIONS[:3])
    calls = []
    first = run_loaded(cfg, all_bars(cfg), fake_processor(calls), stop_after=1)
    assert first["status"]["state"] == "INTERRUPTED"
    assert calls == [("NVDA", SESSIONS[0])]
    second = run_loaded(cfg, all_bars(cfg), fake_processor(calls))
    assert second["status"]["state"] == "COMPLETE"
    assert calls == [("NVDA", day) for day in SESSIONS[:3]]
    fingerprint = second["dataset"]["semantic_fingerprint"]
    third = run_loaded(cfg, all_bars(cfg), fake_processor(calls))
    assert third["dataset"]["semantic_fingerprint"] == fingerprint
    assert len(calls) == 3


def test_incomplete_failed_and_unavailable_are_never_complete(tmp_path):
    cfg = config(tmp_path, symbols=("NVDA",), sessions=SESSIONS[:3])
    bars = all_bars(cfg)
    bars.pop(("NVDA", SESSIONS[2]))
    result = run_loaded(cfg, bars, fake_processor([], incomplete=("NVDA", SESSIONS[0]),
                                                   failed=("NVDA", SESSIONS[1])))
    coverage = result["manifest"]["coverage"]
    assert coverage["sessions_completed"] == 0
    assert coverage["sessions_incomplete"] == 1
    assert coverage["sessions_failed"] == 1
    assert coverage["sessions_unavailable"] == 1
    assert len(result["manifest"]["failures_and_exclusions"]) == 3


def test_compatibility_change_rejects_reuse_and_separates_replay(tmp_path):
    first = config(tmp_path, symbols=("NVDA",), sessions=SESSIONS[:1], sha="a" * 40)
    run_loaded(first, all_bars(first), fake_processor([]))
    second = config(tmp_path, symbols=("NVDA",), sessions=SESSIONS[:1], sha="b" * 40)
    assert first.research_run_id != second.research_run_id
    assert compatibility("b" * 40)["source_mode"] == SOURCE_MODE == "REPLAY"
    run_loaded(second, all_bars(second), fake_processor([]))
    assert len(list((second.output_directory / "units").glob("*.json"))) == 2


def test_corrupt_completed_unit_cannot_masquerade_as_complete(tmp_path):
    cfg = config(tmp_path, symbols=("NVDA",), sessions=SESSIONS[:1])
    run_loaded(cfg, all_bars(cfg), fake_processor([]))
    path = next((cfg.output_directory / "units").glob("*.json"))
    value = json.loads(path.read_text())
    value["unit_key"] = "corrupt"
    path.write_text(json.dumps(value))
    with pytest.raises(CompatibilityError):
        run_loaded(cfg, all_bars(cfg), fake_processor([]))


def test_partition_requires_defensible_history_and_is_chronological():
    assert chronological_partitions(SESSIONS[:4])["status"] == "INSUFFICIENT_HISTORY"
    parts = chronological_partitions(SESSIONS)
    assert list(parts["assignments"].values()) == ["DEVELOPMENT"] * 3 + ["VALIDATION", "HOLDOUT"]
    assert "UNTOUCHED" in parts["holdout_policy"]


def test_actual_pipeline_is_deterministic_and_point_in_time(tmp_path):
    session = date(2026, 8, 12)
    bars = [bar for bar in market_bars() if bar.timestamp.date() == session]
    first = ResearchRunConfig(("NVDA",), (session,), tmp_path / "one", "a" * 40)
    second = ResearchRunConfig(("NVDA",), (session,), tmp_path / "two", "a" * 40)
    one = run_loaded(first, {("NVDA", session): bars})
    two = run_loaded(second, {("NVDA", session): bars})
    assert one["dataset"]["semantic_fingerprint"] == two["dataset"]["semantic_fingerprint"]
    assert one["manifest"]["semantic_fingerprint"] == two["manifest"]["semantic_fingerprint"]
    assert one["manifest"]["coverage"]["not_observed_cycles"] > 0
    assert one["manifest"]["coverage"]["sessions_incomplete"] == 1
    assert all(row["source_mode"] == "REPLAY" for row in one["dataset"]["records"])
    assert all(row["reference_observation_id"].startswith("obs_")
               for row in one["dataset"]["records"])


def test_module_has_no_production_mutation_or_notification_dependency():
    source = __import__("inspect").getsource(__import__("app.research_dataset", fromlist=["x"]))
    imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert not any("notification" in line.lower() or "broker" in line.lower() for line in imports)
    assert "evaluate_production" not in source
