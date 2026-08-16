"""Run Phase 4E over an existing accepted replay ledger and versioned bar cache."""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import tempfile
from pathlib import Path
from time import perf_counter

from app.excursion_analytics import build_excursion_artifact, canonical_json
from app.models import Bar
from app.outcome_evaluator import evaluate_ledger_loaded
from app.persistence import create_stores


def _bars(path: Path) -> list[Bar]:
    return [Bar.model_validate(row) for row in json.loads(path.read_text())]


def run(database: Path, bar_paths: dict[str, Path]) -> dict:
    bars_by_symbol = {symbol.upper(): _bars(path) for symbol, path in sorted(bar_paths.items())}
    with tempfile.TemporaryDirectory(prefix="phase4e-real-validation-") as directory:
        copied = Path(directory) / "replay.sqlite3"
        shutil.copyfile(database, copied)
        stores = create_stores(f"sqlite:///{copied}", initialize=False)
        try:
            outcomes = evaluate_ledger_loaded(stores.research, bars_by_symbol)
            kwargs = {"horizons": (5, 15, 30, 60, 120, "END_OF_SESSION"),
                "cohort_keys": ("horizon_id", "setup_family", "direction"),
                "slice_keys": ("structural_space_atr",)}
            started = perf_counter()
            first = build_excursion_artifact(stores.research, bars_by_symbol, **kwargs)
            elapsed = perf_counter() - started
            second = build_excursion_artifact(stores.research, bars_by_symbol, **kwargs)
            assert canonical_json(first) == canonical_json(second)
            end_rows = [row for row in first["measurements"] if row["horizon_id"] == "END_OF_SESSION"]
            complete = [row for row in end_rows if row["coverage"]["complete"]]
            values = lambda metric: sorted(row[metric]["r"] for row in complete
                                             if row[metric]["r"] is not None)
            summarize = lambda rows: ({"n": len(rows), "min": min(rows), "median": statistics.median(rows),
                "mean": statistics.fmean(rows), "max": max(rows)} if rows else
                {"n": 0, "min": None, "median": None, "mean": None, "max": None})
            threshold = {}
            for key in ("0.5R", "1R", "1.5R", "2R", "3R"):
                eligible = [row["thresholds"][key] for row in complete
                            if row["thresholds"][key]["reached"] is not None]
                hits = [row for row in eligible if row["reached"]]
                threshold[key] = {"n": len(eligible), "rate": len(hits) / len(eligible) if eligible else None,
                    "time_seconds": summarize(sorted(row["time_to_seconds"] for row in hits))}
            successful_heat = sorted(row["mae_before_meaningful_favorable"]["r"]
                for row in complete if row["thresholds"]["1R"]["reached"] is True
                and row["mae_before_meaningful_favorable"]["r"] is not None
                and "MAE_AND_MEANINGFUL_FAVORABLE_SAME_MINUTE" not in row["ambiguity_reasons"])
            return {"scope": "BOUNDED_EXISTING_REAL_REPLAY_NOT_STRATEGY_SELECTION",
                "symbols": sorted(bars_by_symbol), "unique_setup_instances": len(end_rows),
                "evaluable": len(complete), "incomplete": sum(not row["coverage"]["complete"] for row in end_rows),
                "ambiguous": sum(row["ambiguous"] for row in end_rows),
                "mfe_r": summarize(values("mfe")), "mae_r": summarize(values("mae")),
                "thresholds": threshold, "successful_mae_before_success_r": summarize(successful_heat),
                "cohorts": [{"cohort": row["cohort"], "slice": row["slice"],
                    "denominator": row["denominator"], "mfe_r": row["metrics"]["mfe_r"],
                    "mae_r": row["metrics"]["mae_r"]} for row in first["aggregates"]],
                "outcome_batch": outcomes.__dict__,
                "deterministic_repeat": True, "artifact_fingerprint": first["artifact_fingerprint"],
                "build_seconds": elapsed,
                "measurements_per_second": len(first["measurements"]) / elapsed if elapsed else None}
        finally:
            stores.signal.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--bars", action="append", required=True,
                        help="SYMBOL=/path/to/versioned-minute-bars.json")
    args = parser.parse_args()
    mappings = {item.split("=", 1)[0]: Path(item.split("=", 1)[1]) for item in args.bars}
    print(json.dumps(run(args.database, mappings), sort_keys=True, separators=(",", ":")))
