"""
wasm_translator.py — translation between WASM, the DSL, and WAT text.

Three directions:
  - wasm_instrs_to_dsl(instr_dicts)  : extractor JSON instructions -> DSL Instr list
  - dsl_to_wat_module(program, n)    : DSL program -> a full WAT module string
                                       (so we can compile & run it in Wasmtime)
  - the reverse WAT op -> DSL mapping lives in dsl.WAT_TO_DSL

Unsupported instructions are rejected with a clear error rather than silently
dropped. That is deliberate: a superoptimizer that quietly ignores an opcode it
does not understand could "prove" two programs equivalent when they are not.
"""

from __future__ import annotations

from src.dsl import Instr, OPSPECS, WAT_TO_DSL, DSLError


class UnsupportedInstruction(DSLError):
    """Raised when a WASM/WAT instruction is outside the V1 whitelist."""


def wasm_instr_to_dsl(instr: dict) -> Instr:
    """
    Convert one extractor instruction dict to a DSL Instr.

    Expected shapes:
      {"op": "local.get", "index": 0}
      {"op": "i32.const", "value": 0}
      {"op": "i32.add"}
    """
    op = instr["op"]
    if op == "local.get":
        return Instr("PUSH_INPUT", int(instr["index"]))
    if op == "i32.const":
        return Instr("PUSH_CONST", int(instr["value"]))
    if op in WAT_TO_DSL:
        return Instr(WAT_TO_DSL[op])
    raise UnsupportedInstruction(f"Unsupported WASM instruction: {op!r}")


def wasm_instrs_to_dsl(instrs: list[dict]) -> list[Instr]:
    """Convert a list of extractor instruction dicts into a DSL program."""
    return [wasm_instr_to_dsl(i) for i in instrs]


def dsl_to_wat_instr(instr: Instr) -> str:
    """Render a single DSL instruction as a WAT line."""
    spec = OPSPECS[instr.op]
    if instr.op == "PUSH_INPUT":
        return f"local.get {instr.operand}"
    if instr.op == "PUSH_CONST":
        return f"i32.const {instr.operand}"
    return spec.wat


def dsl_to_wat_module(program: list[Instr], num_inputs: int,
                      export_name: str = "f") -> str:
    """
    Wrap a DSL program in a complete WAT module so it can be compiled and run.

    The function takes `num_inputs` i32 params and returns one i32.
    """
    params = " ".join("i32" for _ in range(num_inputs))
    body = "\n    ".join(dsl_to_wat_instr(i) for i in program)
    return (
        "(module\n"
        f'  (func (export "{export_name}") (param {params}) (result i32)\n'
        f"    {body}))\n"
    )
