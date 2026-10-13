"""
difftest.py — differential testing.

Run the original and candidate DSL programs on many concrete inputs and check
they agree. This is a FAST FILTER, not a proof: it samples finitely many inputs,
so agreement is evidence of equivalence, never a guarantee. Z3 supplies the
all-inputs guarantee.

Each program is compiled to a tiny WASM module (via WABT's wat2wasm) and executed
with the Wasmtime Python API. We feed the SAME inputs to both and compare the i32
results. The first disagreement is recorded as a counterexample and testing stops.

Inputs tested:
  - fixed edge cases: 0, 1, -1, 2, INT32_MIN, INT32_MAX, and a few bit patterns,
  - the full cross-product of edge cases across all parameters,
  - plus random i32 vectors up to the configured count.

Requires `wat2wasm` (WABT) on PATH and the `wasmtime` Python package.
"""

from __future__ import annotations

import itertools
import random
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from wasmtime import Engine, Store, Module, Instance

from src.dsl import Instr, interpret, to_s32, to_u32
from src.wasm_translator import dsl_to_wat_module

INT32_MIN = -(2 ** 31)
INT32_MAX = 2 ** 31 - 1
EDGE_VALUES = [0, 1, -1, 2, -2, INT32_MIN, INT32_MAX, 0x7FFFFFFF, -0x80000000,
               0x55555555, -0x55555556]


@dataclass
class DiffResult:
    passed: bool
    tests: int
    mismatches: int
    counterexample: dict | None = None
    note: str = ""


class _CompiledFn:
    """A compiled WASM function ready to call with Python ints."""

    def __init__(self, engine: Engine, wat: str):
        exe = shutil.which("wat2wasm")
        if exe is None:
            raise RuntimeError("wat2wasm not found on PATH (install WABT).")
        with tempfile.TemporaryDirectory() as d:
            wat_path = Path(d) / "m.wat"
            wasm_path = Path(d) / "m.wasm"
            wat_path.write_text(wat)
            proc = subprocess.run([exe, str(wat_path), "-o", str(wasm_path)],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"wat2wasm failed:\n{proc.stderr}\nWAT:\n{wat}")
            self.store = Store(engine)
            module = Module.from_file(engine, str(wasm_path))
            self.fn = Instance(self.store, module, []).exports(self.store)["f"]

    def __call__(self, inputs: list[int]) -> int:
        # Wasmtime expects signed i32 Python ints; results come back signed.
        args = [to_s32(v) for v in inputs]
        res = self.fn(self.store, *args)
        return to_u32(res)


def differential_test(
    original: list[Instr],
    candidate: list[Instr],
    num_inputs: int,
    *,
    num_tests: int = 1000,
    seed: int = 1234,
    use_reference: bool = False,
) -> DiffResult:
    """
    Compare original vs candidate on edge + random inputs.

    If `use_reference` is True, the pure-Python interpreter is used instead of
    Wasmtime. That makes the module importable and testable even on a machine
    without WABT/Wasmtime; the default path uses real WASM execution.
    """
    if use_reference:
        orig_fn = lambda xs: interpret(original, xs)   # noqa: E731
        cand_fn = lambda xs: interpret(candidate, xs)  # noqa: E731
    else:
        engine = Engine()
        orig_fn = _CompiledFn(engine, dsl_to_wat_module(original, num_inputs))
        cand_fn = _CompiledFn(engine, dsl_to_wat_module(candidate, num_inputs))

    rng = random.Random(seed)
    count = 0

    # 1) cross-product of edge values across all params (bounded)
    edge_vectors = list(itertools.product(EDGE_VALUES, repeat=num_inputs))
    if len(edge_vectors) > num_tests:
        edge_vectors = edge_vectors[:num_tests]

    def run_vector(vec: list[int]) -> dict | None:
        nonlocal count
        count += 1
        o = orig_fn(vec)
        c = cand_fn(vec)
        if o != c:
            return {
                "inputs": {f"in{i}": to_s32(v) for i, v in enumerate(vec)},
                "original": to_s32(o),
                "candidate": to_s32(c),
            }
        return None

    for vec in edge_vectors:
        ce = run_vector(list(vec))
        if ce:
            return DiffResult(False, count, 1, ce,
                              note="Edge-case mismatch; candidate rejected early.")

    # 2) random vectors to fill up to num_tests
    while count < num_tests:
        vec = [rng.randint(INT32_MIN, INT32_MAX) for _ in range(num_inputs)]
        ce = run_vector(vec)
        if ce:
            return DiffResult(False, count, 1, ce,
                              note="Random mismatch; candidate rejected.")

    return DiffResult(True, count, 0,
                      note="Agreed on all sampled inputs (evidence, not proof).")


if __name__ == "__main__":
    from src.dsl import parse
    o = parse(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"])
    c = parse(["PUSH_INPUT 0"])
    print("x+0 vs x ->", differential_test(o, c, 1, num_tests=500))
    bad = parse(["PUSH_INPUT 0", "PUSH_CONST 2", "ADD"])
    print("x+0 vs x+2 ->", differential_test(o, bad, 1, num_tests=500))
