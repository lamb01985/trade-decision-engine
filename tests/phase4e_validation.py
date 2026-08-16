"""Bounded Phase 4E replay-evidence validation; never strategy selection."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from time import perf_counter

from app.engines.setups import CandidateEvidence
from app.excursion_analytics import build_excursion_artifact, canonical_json
from app.models import SetupFamily, SetupProgress
from app.outcome_evaluator import evaluate_ledger_loaded
from app.parameters import DEFAULT_PARAMETERS
from app.persistence import create_stores
from tests.test_outcome_evaluator import T0, bar, full_session
from tests.test_research_ledger import analysis


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="phase4e-validation-") as directory:
        stores = create_stores(f"sqlite:///{Path(directory) / 'phase4e.sqlite3'}", initialize=True)
        try:
            cases = (
                ("LONGRUN", "LONG", SetupFamily.BREAKOUT_RETEST,
                 full_session({1: bar(1, high=100.6, low=99.7), 3: bar(3, high=101.2),
                               8: bar(8, high=102.1)})),
                ("SHORTMOVE", "SHORT", SetupFamily.VWAP_REJECTION,
                 full_session({1: bar(1, low=99.4, high=100.2), 4: bar(4, low=98.8)})),
                ("AMBIG", "LONG", SetupFamily.BREAKOUT_RETEST,
                 full_session({1: bar(1, high=101.2, low=98.8)})),
                ("GAPPED", "SHORT", SetupFamily.VWAP_REJECTION, full_session()[1:]),
            )
            bars_by_symbol = {}
            for symbol, direction, family, evidence in cases:
                current = analysis(T0, SetupProgress.CONFIRMED)
                current = current.model_copy(update={
                    "instrument": current.instrument.model_copy(update={"symbol": symbol}),
                    "decision": current.decision.model_copy(update={"symbol": symbol,
                        "direction": direction, "setup_family": family,
                        "invalidation": 99.0 if direction == "LONG" else 101.0,
                        "target_1": 101.0 if direction == "LONG" else 99.0,
                        "target_2": 102.0 if direction == "LONG" else 98.0})})
                candidate = CandidateEvidence(family, direction, SetupProgress.CONFIRMED, 100.0,
                    ("bounded validation",), (), "MAPPED_RESISTANCE",
                    {"price": 100.0, "level_types": ["PRIOR_DAY_HIGH"]})
                stores.research.observe_candidates(current, [candidate], DEFAULT_PARAMETERS,
                    source_mode="REPLAY", replay_run_id="rpl_phase4e_bounded_v1")
                bars_by_symbol[symbol] = evidence
            evaluate_ledger_loaded(stores.research, bars_by_symbol)
            kwargs = {"horizons": (5, 30, "END_OF_SESSION"),
                "cohort_keys": ("setup_family", "direction"),
                "slice_keys": ("structural_space_atr",)}
            started = perf_counter()
            first = build_excursion_artifact(stores.research, bars_by_symbol, **kwargs)
            elapsed = perf_counter() - started
            second = build_excursion_artifact(stores.research, bars_by_symbol, **kwargs)
            assert canonical_json(first) == canonical_json(second)
            end_rows = [row for row in first["measurements"] if row["horizon_id"] == "END_OF_SESSION"]
            complete = [row for row in end_rows if row["coverage"]["complete"]]
            dist = lambda field: sorted(row[field]["r"] for row in complete if row[field]["r"] is not None)
            threshold_rates = {key: sum(row["thresholds"][key]["reached"] is True for row in complete) /
                len(complete) if complete else None for key in ("0.5R", "1R", "2R", "3R")}
            return {"validation_scope": "BOUNDED_REPLAY_EVIDENCE_NOT_STRATEGY_SELECTION",
                "unique_setup_instances": len(end_rows), "evaluable": len(complete),
                "incomplete": sum(not row["coverage"]["complete"] for row in end_rows),
                "ambiguous": sum(row["ambiguous"] for row in end_rows),
                "mfe_r_distribution": dist("mfe"), "mae_r_distribution": dist("mae"),
                "mfe_price_distribution": sorted(row["mfe"]["price"] for row in complete),
                "mae_price_distribution": sorted(row["mae"]["price"] for row in complete),
                "threshold_hit_rates": threshold_rates,
                "time_to_threshold_seconds": {key: sorted(row["thresholds"][key]["time_to_seconds"]
                    for row in complete if row["thresholds"][key]["time_to_seconds"] is not None)
                    for key in ("0.5R", "1R", "2R", "3R")},
                "successful_mae_before_success_r": sorted(row["mae_before_meaningful_favorable"]["r"]
                    for row in complete if row["thresholds"]["1R"]["reached"] is True
                    and row["mae_before_meaningful_favorable"]["r"] is not None),
                "aggregates_by_family_direction": [{"cohort": row["cohort"],
                    "denominator": row["denominator"], "mfe_r": row["metrics"]["mfe_r"],
                    "mae_r": row["metrics"]["mae_r"], "room_to_run_slice": row["slice"]}
                    for row in first["aggregates"]],
                "artifact_fingerprint": first["artifact_fingerprint"],
                "deterministic_repeat": True, "build_seconds": elapsed,
                "measurement_throughput_per_second": len(first["measurements"]) / elapsed}
        finally:
            stores.signal.close()


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True, separators=(",", ":")))
