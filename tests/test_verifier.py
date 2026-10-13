"""Unit tests for the Z3 verifier (correctness backbone)."""
from src.dsl import parse
from src.verifier import verify


def _v(orig, cand, n=1):
    return verify(parse(orig), parse(cand), n)


def test_x_plus_zero_equivalent():
    r = _v(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"], ["PUSH_INPUT 0"])
    assert r.status == "EQUIVALENT" and r.accepted and r.z3_result == "unsat"


def test_x_plus_one_not_equivalent():
    r = _v(["PUSH_INPUT 0", "PUSH_CONST 1", "ADD"], ["PUSH_INPUT 0"])
    assert r.status == "NOT_EQUIVALENT" and not r.accepted
    assert r.counterexample is not None


def test_x_times_two_equals_x_plus_x():
    r = _v(["PUSH_INPUT 0", "PUSH_CONST 2", "MUL"],
           ["PUSH_INPUT 0", "PUSH_INPUT 0", "ADD"])
    assert r.status == "EQUIVALENT"


def test_x_plus_one_vs_x_plus_two():
    r = _v(["PUSH_INPUT 0", "PUSH_CONST 1", "ADD"],
           ["PUSH_INPUT 0", "PUSH_CONST 2", "ADD"])
    assert r.status == "NOT_EQUIVALENT"


def test_x_times_zero_equals_zero():
    r = _v(["PUSH_INPUT 0", "PUSH_CONST 0", "MUL"], ["PUSH_CONST 0"])
    assert r.status == "EQUIVALENT"


def test_x_times_one_equals_x():
    r = _v(["PUSH_INPUT 0", "PUSH_CONST 1", "MUL"], ["PUSH_INPUT 0"])
    assert r.status == "EQUIVALENT"


def test_x_minus_zero_equals_x():
    r = _v(["PUSH_INPUT 0", "PUSH_CONST 0", "SUB"], ["PUSH_INPUT 0"])
    assert r.status == "EQUIVALENT"


def test_x_or_zero_equals_x():
    r = _v(["PUSH_INPUT 0", "PUSH_CONST 0", "OR"], ["PUSH_INPUT 0"])
    assert r.status == "EQUIVALENT"


def test_x_xor_zero_equals_x():
    r = _v(["PUSH_INPUT 0", "PUSH_CONST 0", "XOR"], ["PUSH_INPUT 0"])
    assert r.status == "EQUIVALENT"


def test_shift_count_mod_32():
    # x << 40 is the same as x << 8 under WASM semantics.
    r = _v(["PUSH_INPUT 0", "PUSH_CONST 40", "SHL"],
           ["PUSH_INPUT 0", "PUSH_CONST 8", "SHL"])
    assert r.status == "EQUIVALENT"


def test_shift_naive_would_be_wrong():
    # x << 40 is NOT equivalent to pushing 0 (which a broken >>32 model might give)
    r = _v(["PUSH_INPUT 0", "PUSH_CONST 40", "SHL"], ["PUSH_CONST 0"])
    assert r.status == "NOT_EQUIVALENT"
