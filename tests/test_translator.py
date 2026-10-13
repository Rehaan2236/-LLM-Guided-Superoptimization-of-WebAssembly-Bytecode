"""Unit tests for the WASM<->DSL translator."""
import pytest

from src.dsl import serialize
from src.wasm_translator import (
    wasm_instrs_to_dsl, dsl_to_wat_module, UnsupportedInstruction,
)
from src.dsl import parse


def test_wasm_instrs_to_dsl():
    instrs = [
        {"op": "local.get", "index": 0},
        {"op": "i32.const", "value": 0},
        {"op": "i32.add"},
    ]
    prog = wasm_instrs_to_dsl(instrs)
    assert serialize(prog) == ["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"]


def test_unsupported_instruction_rejected():
    with pytest.raises(UnsupportedInstruction):
        wasm_instrs_to_dsl([{"op": "i32.div_s"}])


def test_dsl_to_wat_module_has_params_and_body():
    prog = parse(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"])
    wat = dsl_to_wat_module(prog, 1)
    assert "(param i32)" in wat
    assert "(result i32)" in wat
    assert "local.get 0" in wat and "i32.const 0" in wat and "i32.add" in wat
