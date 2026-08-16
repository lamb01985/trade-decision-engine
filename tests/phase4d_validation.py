"""Bounded deterministic Phase 4D mechanics validation (not strategy research)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.engines.setups import CandidateEvidence
from app.evidence_analytics import build_evidence_artifact, canonical_json
from app.models import SetupFamily, SetupProgress
from app.outcome_evaluator import evaluate_ledger_loaded
from app.parameters import DEFAULT_PARAMETERS
from app.persistence import create_stores
from tests.test_outcome_evaluator import T0, bar, full_session
from tests.test_research_ledger import analysis


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="phase4d-validation-") as directory:
        stores = create_stores(f"sqlite:///{Path(directory) / 'evidence.sqlite3'}", initialize=True)
        bars_by_symbol = {}
        try:
            cases = (
                ("LONGWIN", "LONG", full_session({1: bar(1, high=101.1)})),
                ("SHORTLOSS", "SHORT", full_session({1: bar(1, high=101.1)})),
                ("AMBIG", "LONG", full_session({1: bar(1, low=98.9, high=101.1)})),
                ("INCOMPLETE", "LONG", full_session()[1:]),
            )
            for symbol, direction, bars in cases:
                current = analysis(T0, SetupProgress.CONFIRMED)
                instrument = current.instrument.model_copy(update={"symbol": symbol})
                decision = current.decision.model_copy(update={"symbol": symbol, "direction": direction,
                    "invalidation": 99.0 if direction == "LONG" else 101.0,
                    "target_1": 101.0 if direction == "LONG" else 99.0,
                    "target_2": 102.0 if direction == "LONG" else 98.0})
                current = current.model_copy(update={"instrument": instrument, "decision": decision})
                candidate = CandidateEvidence(SetupFamily.BREAKOUT_RETEST, direction,
                    SetupProgress.CONFIRMED, 100.0, ("bounded validation",), (),
                    "MAPPED_RESISTANCE", {"price": 100.0,
                                           "level_types": ["PRIOR_DAY_HIGH"]})
                stores.research.observe_candidates(current, [candidate], DEFAULT_PARAMETERS,
                    source_mode="REPLAY", replay_run_id="rpl_phase4d_bounded_v1")
                bars_by_symbol[symbol] = bars
            batch = evaluate_ledger_loaded(stores.research, bars_by_symbol)
            kwargs = {"cohort_keys": ("setup_family", "direction"),
                      "slice_keys": ("time_of_day", "relative_volume")}
            first = build_evidence_artifact(stores.research, **kwargs)
            second = build_evidence_artifact(stores.research, **kwargs)
            assert canonical_json(first) == canonical_json(second)
            assert first["artifact_fingerprint"] == second["artifact_fingerprint"]
            denominators = {row["cohort"]["direction"]: row["denominator"]
                            for row in first["aggregates"]}
            return {"validation_scope": "MECHANICS_ONLY_NOT_STRATEGY_EVIDENCE",
                "ready_eligible": batch.ready_eligible, "inserted": batch.inserted,
                "target_first": batch.target_first,
                "invalidation_first": batch.invalidation_first,
                "ambiguous": batch.ambiguous, "neither": batch.neither,
                "denominators": denominators,
                "sample_labels": {row["cohort"]["direction"]: row["sample_label"]
                                  for row in first["aggregates"]},
                "context_slice": first["aggregates"][0]["slice"],
                "generation_input_fingerprint": first["generation_input_fingerprint"],
                "artifact_fingerprint": first["artifact_fingerprint"],
                "deterministic_repeat": True}
        finally:
            stores.signal.close()


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True, separators=(",", ":")))
