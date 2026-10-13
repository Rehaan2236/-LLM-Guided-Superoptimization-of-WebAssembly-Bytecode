"""
superopt_search.py — enumerative superoptimizer + "wasm-opt miss" hunter.

This is the classical superoptimizer baseline the project needs, and the engine
that produces a genuine novelty result. It works in four stages:

  1. ENUMERATE every valid straight-line DSL program up to a length bound over a
     chosen opcode set and input count (bounded brute force).
  2. BUCKET programs by behavioral signature: the tuple of outputs on a fixed set
     of probe inputs. Programs with the same signature are equivalence CANDIDATES.
  3. For each bucket, take the shortest program as the "target". For every longer
     program in the bucket, treat (longer -> shorter) as a rewrite and CONFIRM it
     with Z3 (all-inputs proof). Only Z3-confirmed rewrites are kept.
  4. For each confirmed rewrite, compile the ORIGINAL to wasm, run `wasm-opt -Oz`,
     and measure the instruction count wasm-opt achieves. If wasm-opt's result is
     STILL LONGER than our verified rewrite, it is a genuine wasm-opt MISS.

Everything is real: Z3 proves equivalence, wasm-opt is actually invoked, and no
result is fabricated. Discoveries are written to results/discoveries.{json,csv}.

Usage:
  python -m src.superopt_search                 # default: 2 inputs, len<=4
  python -m src.superopt_search --inputs 2 --max-len 4 --consts 0 1 -1
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from src.dsl import (
    Instr, OPSPECS, serialize, validate, interpret, instruction_count, DSLError,
)
from src.verifier import verify
from src.wasm_translator import dsl_to_wat_module

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"

# Opcodes the search is allowed to use. Unary EQZ plus the binary ops; PUSH_INPUT
# and PUSH_CONST are added per-config. Count ops (clz/ctz/popcnt) are excluded
# because they are not modeled.
BINARY_OPS = ["ADD", "SUB", "MUL", "AND", "OR", "XOR", "SHL", "SHR_S", "SHR_U",
              "ROTL", "ROTR", "EQ", "NE", "LT_S", "LT_U", "GT_S", "GT_U",
              "LE_S", "LE_U", "GE_S", "GE_U"]
UNARY_OPS = ["EQZ"]

# Fixed probe inputs for behavioral bucketing. Chosen to separate tricky cases
# (signs, boundaries, bit patterns). More probes = fewer false buckets.
PROBE_SCALARS = [0, 1, 2, 3, -1, -2, 7, 255, 256, 65535,
                 2**31 - 1, -(2**31), 0x55555555, -0x55555556, 123456789, -987654321]


@dataclass
class Discovery:
    original: list[str]
    rewrite: list[str]
    num_inputs: int
    original_instr: int
    rewrite_instr: int
    wasmopt_instr: int
    wasmopt_misses: bool
    note: str = ""


def _gen_programs(num_inputs: int, max_len: int, consts: list[int]):
    """Yield every stack-valid program (as tuple[Instr,...]) up to max_len."""
    pushes = [Instr("PUSH_INPUT", i) for i in range(num_inputs)]
    pushes += [Instr("PUSH_CONST", c) for c in consts]
    ops = [Instr(op) for op in BINARY_OPS + UNARY_OPS]
    alphabet = pushes + ops

    # DFS over sequences, pruning by running stack depth.
    def rec(prefix: list[Instr], depth: int):
        if 1 <= depth and len(prefix) >= 1 and depth == 1:
            yield tuple(prefix)
        if len(prefix) == max_len:
            return
        for instr in alphabet:
            spec = OPSPECS[instr.op]
            if depth < spec.pops:
                continue
            nd = depth - spec.pops + spec.pushes
            if nd > 8:
                continue
            prefix.append(instr)
            yield from rec(prefix, nd)
            prefix.pop()

    yield from rec([], 0)


def _signature(prog: tuple[Instr, ...], num_inputs: int) -> tuple[int, ...]:
    """Behavioral fingerprint: outputs on the probe-input vectors."""
    sig = []
    # single-axis probes (vary one input, others fixed) + a few joint probes
    vectors = []
    if num_inputs == 1:
        vectors = [[s] for s in PROBE_SCALARS]
    else:
        base = PROBE_SCALARS[:8]
        for a in base:
            for b in base:
                vectors.append([a, b] + [1] * (num_inputs - 2))
    for vec in vectors:
        sig.append(interpret(list(prog), vec))
    return tuple(sig)


def _wasmopt_instr_count(prog: list[Instr], num_inputs: int) -> int:
    """Compile prog -> wasm, run wasm-opt -Oz, return its body instruction count."""
    exe_opt = shutil.which("wasm-opt")
    exe_w2w = shutil.which("wat2wasm")
    exe_w2wat = shutil.which("wasm2wat")
    if not (exe_opt and exe_w2w and exe_w2wat):
        return -1
    with tempfile.TemporaryDirectory() as d:
        wat = Path(d) / "m.wat"
        wasm = Path(d) / "m.wasm"
        opt = Path(d) / "opt.wasm"
        wat.write_text(dsl_to_wat_module(prog, num_inputs))
        subprocess.run([exe_w2w, str(wat), "-o", str(wasm)], capture_output=True)
        subprocess.run([exe_opt, "-Oz", str(wasm), "-o", str(opt)], capture_output=True)
        txt = subprocess.run([exe_w2wat, str(opt)], capture_output=True, text=True).stdout
    # count body instructions (lines inside func that aren't headers)
    count = 0
    for line in txt.splitlines():
        s = line.strip().rstrip(")").strip()
        if not s or s.startswith("(module") or s.startswith("(type") \
                or s.startswith("(func") or s.startswith("(param") \
                or s.startswith("(result") or s.startswith("(export") \
                or s.startswith("(local") or s == "func":
            continue
        count += 1
    return count


def search(num_inputs: int, max_len: int, consts: list[int],
           *, verbose: bool = True) -> list[Discovery]:
    t0 = time.time()
    # 1) enumerate + validate
    buckets: dict[tuple, list[tuple[Instr, ...]]] = {}
    n_progs = 0
    for prog in _gen_programs(num_inputs, max_len, consts):
        try:
            validate(list(prog), num_inputs, max_len=max_len, max_stack=8)
        except DSLError:
            continue
        n_progs += 1
        sig = _signature(prog, num_inputs)
        buckets.setdefault(sig, []).append(prog)
    if verbose:
        print(f"Enumerated {n_progs} valid programs into {len(buckets)} behavior buckets "
              f"({time.time()-t0:.1f}s)")

    # 2+3) within each multi-member bucket, confirm shorter rewrites with Z3
    discoveries: list[Discovery] = []
    seen_pairs = set()
    checked = 0
    for sig, members in buckets.items():
        if len(members) < 2:
            continue
        members.sort(key=len)
        target = members[0]           # shortest in the bucket
        tlen = len(target)
        for prog in members[1:]:
            if len(prog) <= tlen:
                continue              # not a reduction
            key = (tuple(serialize(list(prog))), tuple(serialize(list(target))))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            checked += 1
            vr = verify(list(prog), list(target), num_inputs, timeout_ms=10000)
            if not vr.accepted:
                continue              # signature collision, not truly equivalent
            # 4) wasm-opt comparison
            wopt = _wasmopt_instr_count(list(prog), num_inputs)
            misses = wopt > len(target)
            discoveries.append(Discovery(
                original=serialize(list(prog)),
                rewrite=serialize(list(target)),
                num_inputs=num_inputs,
                original_instr=len(prog),
                rewrite_instr=len(target),
                wasmopt_instr=wopt,
                wasmopt_misses=misses,
                note="wasm-opt leaves it longer than our verified rewrite"
                     if misses else "wasm-opt matches or beats our rewrite",
            ))

    discoveries.sort(key=lambda d: (not d.wasmopt_misses,
                                    d.wasmopt_instr - d.rewrite_instr), reverse=False)
    if verbose:
        n_miss = sum(d.wasmopt_misses for d in discoveries)
        print(f"Z3-confirmed {len(discoveries)} shorter rewrites from {checked} candidates; "
              f"{n_miss} are genuine wasm-opt misses ({time.time()-t0:.1f}s total)")
    return discoveries


def write_discoveries(ds: list[Discovery]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": {
            "confirmed_rewrites": len(ds),
            "wasmopt_misses": sum(d.wasmopt_misses for d in ds),
        },
        "discoveries": [asdict(d) for d in ds],
    }
    (RESULTS_DIR / "discoveries.json").write_text(json.dumps(payload, indent=2) + "\n")
    if ds:
        with (RESULTS_DIR / "discoveries.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(asdict(ds[0]).keys()))
            w.writeheader()
            for d in ds:
                row = asdict(d)
                row["original"] = " ; ".join(row["original"])
                row["rewrite"] = " ; ".join(row["rewrite"])
                w.writerow(row)


def main() -> None:
    ap = argparse.ArgumentParser(description="Enumerative superoptimizer / wasm-opt miss hunter")
    ap.add_argument("--inputs", type=int, default=2)
    ap.add_argument("--max-len", type=int, default=4)
    ap.add_argument("--consts", type=int, nargs="*", default=[0, 1, -1, 31])
    args = ap.parse_args()

    ds = search(args.inputs, args.max_len, args.consts)
    write_discoveries(ds)
    misses = [d for d in ds if d.wasmopt_misses]
    print("\n=== GENUINE wasm-opt MISSES (Z3-verified shorter rewrites wasm-opt did not find) ===")
    if not misses:
        print("  none at this bound — try --max-len 5 or more constants")
    for d in misses[:20]:
        print(f"  {d.original}  ==>  {d.rewrite}   "
              f"(ours {d.rewrite_instr} instr, wasm-opt {d.wasmopt_instr})")
    print(f"\nWrote results/discoveries.json and results/discoveries.csv")


if __name__ == "__main__":
    main()
