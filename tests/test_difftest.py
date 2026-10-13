"""
Tests for differential testing. The default path uses Wasmtime; we also test the
pure-Python reference path (use_reference=True) which needs no external tools, so
at least one test always runs.
"""
import shutil

import pytest

from src.dsl import parse
from src.difftest import differential_test

needs_wabt = pytest.mark.skipif(
    shutil.which("wat2wasm") is None, reason="wat2wasm not on PATH"
)


def test_reference_path_agrees_on_x_plus_zero():
    o = parse(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"])
    c = parse(["PUSH_INPUT 0"])
    r = differential_test(o, c, 1, num_tests=200, use_reference=True)
    assert r.passed and r.mismatches == 0


def test_reference_path_catches_mismatch():
    o = parse(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"])
    bad = parse(["PUSH_INPUT 0", "PUSH_CONST 2", "ADD"])
    r = differential_test(o, bad, 1, num_tests=200, use_reference=True)
    assert not r.passed and r.counterexample is not None


@needs_wabt
def test_wasmtime_path_agrees_on_x_plus_zero():
    o = parse(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"])
    c = parse(["PUSH_INPUT 0"])
    r = differential_test(o, c, 1, num_tests=200)
    assert r.passed


@needs_wabt
def test_wasmtime_path_catches_mismatch():
    o = parse(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"])
    bad = parse(["PUSH_INPUT 0", "PUSH_CONST 1", "ADD"])
    r = differential_test(o, bad, 1, num_tests=200)
    assert not r.passed
