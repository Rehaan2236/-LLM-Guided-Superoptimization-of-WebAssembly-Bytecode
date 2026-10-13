"""
experiment.py — initial experiments / results aggregation.

Runs the optimizer over a set of .wasm files, records real counters, and writes
results/results.json and results/results.csv. Also folds in the wasm-opt baseline
so the two can be compared honestly. NO numbers are invented: every field is
produced by actually running the pipeline and wasm-opt.

Usage:
  python -m src.experiment                 # runs over examples/*.wasm
  python -m src.experiment corpus/*.wasm   # runs over a chosen set
"""

from __future__ import annotations

import csv
import json
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

from src.dsl import instruction_count
from src.extractor import extract_blocks
from src.wasm_translator import wasm_instrs_to_dsl
from src.pipeline import optimize_block, load_config
from src.baseline import measure

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"


@dataclass
class Totals:
    blocks_in: int = 0
    blocks_skipped: int = 0
    candidates_accepted: int = 0
    z3_unsat: int = 0
    reductions: int = 0
    instr_saved: int = 0
    rewrites_wasmopt_misses: int = 0  # verified rewrite where wasm-opt did not reduce
    wall_seconds: float = 0.0


def run(paths: list[str]) -> dict:
    cfg = load_config()
    cfg["differential_tests"] = min(cfg["differential_tests"], 500)
    rows: list[dict] = []
    totals = Totals()
    t0 = time.time()

    for p in paths:
        path = Path(p)
        blocks = extract_blocks(path)
        base = measure(path)
        for b in blocks:
            if b.get("skipped"):
                totals.blocks_skipped += 1
                rows.append({
                    "file": path.name, "status": "skipped",
                    "reason": b.get("reason", ""),
                    "original_instr": None, "rewrite_instr": None,
                    "accepted": False, "z3": None,
                    "wasmopt_instr": base.wasmopt_instr,
                })
                continue

            totals.blocks_in += 1
            program = wasm_instrs_to_dsl(b["instructions"])
            res = optimize_block(program, b["params"], cfg,
                                 allow_gemini=False, verbose=False)
            accepted = res["accepted"]
            if accepted:
                totals.candidates_accepted += 1
                if res["z3"] == "unsat":
                    totals.z3_unsat += 1
                saved = res["original_instr"] - (res["rewrite_instr"] or res["original_instr"])
                if saved > 0:
                    totals.reductions += 1
                    totals.instr_saved += saved
                # did wasm-opt also reduce this block?
                wopt = base.wasmopt_instr
                if wopt is not None and wopt >= res["original_instr"] and saved > 0:
                    totals.rewrites_wasmopt_misses += 1

            rows.append({
                "file": path.name, "status": "accepted" if accepted else "unchanged",
                "reason": "",
                "original_instr": res["original_instr"],
                "rewrite_instr": res["rewrite_instr"],
                "accepted": accepted, "z3": res["z3"],
                "wasmopt_instr": base.wasmopt_instr,
            })

    totals.wall_seconds = round(time.time() - t0, 3)
    out = {"totals": asdict(totals), "rows": rows}

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "results.json").write_text(json.dumps(out, indent=2) + "\n")
    with (RESULTS_DIR / "results.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return out


if __name__ == "__main__":
    paths = sys.argv[1:] or [str(p) for p in sorted((ROOT / "examples").glob("*.wasm"))]
    out = run(paths)
    t = out["totals"]
    print("=== INITIAL RESULTS (generated from real runs) ===")
    for k, v in t.items():
        print(f"  {k}: {v}")
    print(f"\nPer-block rows: {len(out['rows'])}")
    print("Wrote results/results.json and results/results.csv")
    print("\nNote: on these identity examples, instruction reductions our pipeline")
    print("finds are also found by wasm-opt. A genuine 'wasm-opt misses' result")
    print("requires a larger corpus and is NOT claimed here.")
