"""
baseline.py — baseline comparison against Binaryen wasm-opt.

For a given .wasm input we measure:
  - original instruction count (in the supported block) and byte size,
  - wasm-opt -Oz instruction count and byte size.

Our own optimized counts are filled in by the pipeline when it has produced a
verified rewrite. This module only measures; it makes NO claim that our method
beats wasm-opt. On the x+0 example, wasm-opt also folds the add, so the honest
finding is parity, not superiority. Novelty only appears if, on some block,
we produce a verified rewrite that wasm-opt does not.

Outputs results/baseline.json and results/baseline.csv.

Requires `wasm-opt` (Binaryen) and `wasm2wat` (WABT) on PATH.
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path

from src.extractor import extract_blocks

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


@dataclass
class BaselineRow:
    file: str
    original_instr: int | None
    original_bytes: int
    wasmopt_instr: int | None
    wasmopt_bytes: int
    note: str = ""


def _count_block_instrs(wasm_path: Path) -> int | None:
    """Instruction count of the first supported block, or None if none."""
    for b in extract_blocks(wasm_path):
        if not b.get("skipped"):
            return len(b["instructions"])
    return None


def run_wasm_opt(wasm_path: Path, out_path: Path, flags: str = "-Oz") -> None:
    exe = shutil.which("wasm-opt")
    if exe is None:
        raise RuntimeError("wasm-opt not found on PATH (install Binaryen).")
    proc = subprocess.run(
        [exe, flags, str(wasm_path), "-o", str(out_path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"wasm-opt failed:\n{proc.stderr}")


def measure(wasm_path: str | Path) -> BaselineRow:
    wasm_path = Path(wasm_path)
    original_bytes = wasm_path.stat().st_size
    original_instr = _count_block_instrs(wasm_path)

    with tempfile.TemporaryDirectory() as d:
        opt_path = Path(d) / "opt.wasm"
        run_wasm_opt(wasm_path, opt_path)
        wasmopt_bytes = opt_path.stat().st_size
        wasmopt_instr = _count_block_instrs(opt_path)

    note = ""
    if original_instr is not None and wasmopt_instr is not None:
        if wasmopt_instr < original_instr:
            note = "wasm-opt reduced the block"
        elif wasmopt_instr == original_instr:
            note = "wasm-opt left the block unchanged"
    return BaselineRow(
        file=wasm_path.name,
        original_instr=original_instr, original_bytes=original_bytes,
        wasmopt_instr=wasmopt_instr, wasmopt_bytes=wasmopt_bytes, note=note,
    )


def measure_many(wasm_paths: list[str | Path]) -> list[BaselineRow]:
    return [measure(p) for p in wasm_paths]


def write_results(rows: list[BaselineRow], results_dir: Path = RESULTS_DIR) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "baseline.json").write_text(
        json.dumps([asdict(r) for r in rows], indent=2) + "\n"
    )
    with (results_dir / "baseline.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow(asdict(r))


if __name__ == "__main__":
    paths = sys.argv[1:] or [
        str(p) for p in (Path(__file__).resolve().parent.parent / "examples").glob("*.wasm")
    ]
    rows = measure_many(paths)
    write_results(rows)
    for r in rows:
        print(f"{r.file}: orig={r.original_instr} instr/{r.original_bytes}B  "
              f"wasm-opt={r.wasmopt_instr} instr/{r.wasmopt_bytes}B  ({r.note})")
    print(f"\nWrote results/baseline.json and results/baseline.csv")
