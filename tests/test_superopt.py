"""
Tests for the V2 opcodes and the novelty findings.

The equivalence tests are pure Z3 (no external tools). The "wasm-opt miss" tests
need wasm-opt + WABT and are skipped otherwise. They lock in the headline result:
a Z3-verified rewrite that wasm-opt -Oz does not find.
"""
import shutil

import pytest

from src.dsl import parse
from src.verifier import verify

needs_wasmopt = pytest.mark.skipif(
    shutil.which("wasm-opt") is None or shutil.which("wat2wasm") is None,
    reason="wasm-opt / wat2wasm not on PATH",
)

# (name, original, rewrite, num_inputs) — each a Z3-verified equivalence.
HEADLINE = [
    ("xor_eqz_is_eq",
     ["PUSH_INPUT 0", "PUSH_INPUT 1", "XOR", "EQZ"],
     ["PUSH_INPUT 0", "PUSH_INPUT 1", "EQ"], 2),
    ("signbit_shrs_is_ge0",
     ["PUSH_INPUT 0", "PUSH_CONST 31", "SHR_S", "EQZ"],
     ["PUSH_INPUT 0", "PUSH_CONST 0", "GE_S"], 1),
    ("signbit_shru_is_ge0",
     ["PUSH_INPUT 0", "PUSH_CONST 31", "SHR_U", "EQZ"],
     ["PUSH_INPUT 0", "PUSH_CONST 0", "GE_S"], 1),
    ("not_eqz_is_ne0",
     ["PUSH_CONST 1", "PUSH_INPUT 0", "EQZ", "SUB"],
     ["PUSH_INPUT 0", "PUSH_CONST 0", "NE"], 1),
]


# --- V2 opcode soundness (pure Z3) ---------------------------------------
def test_v2_opcodes_present():
    from src.dsl import OPSPECS
    for op in ("ROTL", "ROTR", "LT_S", "LT_U", "GT_S", "GT_U",
               "LE_S", "LE_U", "GE_S", "GE_U"):
        assert op in OPSPECS


def test_rotl_rotr_inverse():
    # rotl(x, k) then rotr by k returns x  (for a fixed k)
    r = verify(parse(["PUSH_INPUT 0", "PUSH_CONST 7", "ROTL",
                      "PUSH_CONST 7", "ROTR"]),
               parse(["PUSH_INPUT 0"]), 1)
    assert r.status == "EQUIVALENT"


def test_comparison_pair_lt_ge():
    # (x <s y) and (x >=s y) can never both be 1 — check x<s y  !=  x>=s y always
    r = verify(parse(["PUSH_INPUT 0", "PUSH_INPUT 1", "LT_S"]),
               parse(["PUSH_INPUT 0", "PUSH_INPUT 1", "GE_S"]), 2)
    assert r.status == "NOT_EQUIVALENT"


# --- Headline equivalences (pure Z3) -------------------------------------
@pytest.mark.parametrize("name,orig,rewrite,n", HEADLINE,
                         ids=[h[0] for h in HEADLINE])
def test_headline_equivalences(name, orig, rewrite, n):
    r = verify(parse(orig), parse(rewrite), n)
    assert r.status == "EQUIVALENT", f"{name} should be equivalent"


# --- The actual novelty claim: wasm-opt leaves these longer ---------------
@needs_wasmopt
@pytest.mark.parametrize("name,orig,rewrite,n", HEADLINE,
                         ids=[h[0] for h in HEADLINE])
def test_wasmopt_misses(name, orig, rewrite, n):
    from src.superopt_search import _wasmopt_instr_count
    from src.dsl import parse as p
    wopt = _wasmopt_instr_count(p(orig), n)
    assert wopt > len(rewrite), (
        f"{name}: wasm-opt got {wopt} instr, our verified rewrite is {len(rewrite)}"
    )
