"""
dsl.py — Constrained bitvector DSL for the WASM superoptimizer .

This module is the foundation everything else is built on. It defines:
  - the instruction set (whitelist),
  - a parser (list[str] -> list[Instr]),
  - a serializer (list[Instr] -> list[str]),
  - a stack validator (arity + depth + single result),
  - a pure-Python reference interpreter using 32-bit modular arithmetic.

Why a DSL instead of raw WASM?
  Making the LLM emit raw WebAssembly is error-prone (it produces invalid
  modules). A tiny, constrained DSL is easy to generate, easy to parse,
  easy to validate, and translates cleanly into Z3 bitvectors.

Design notes:
  - A program is a straight-line sequence of instructions operating on a stack.
  - "Inputs" are the function parameters (locals 0..n-1).
  - A valid program ends with exactly ONE value on the stack (the result).
  - All values are 32-bit. Arithmetic wraps mod 2**32. Signedness lives in the
    operation (SHR_S vs SHR_U), not in the value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

MASK32 = 0xFFFFFFFF


# --------------------------------------------------------------------------
# 32-bit helpers (concrete reference semantics)
# --------------------------------------------------------------------------
def to_u32(x: int) -> int:
    """Reduce any Python int to an unsigned 32-bit value (0 .. 2**32-1)."""
    return x & MASK32


def to_s32(x: int) -> int:
    """Interpret the low 32 bits of x as a signed two's-complement int."""
    x &= MASK32
    return x - (1 << 32) if x & 0x80000000 else x


# --------------------------------------------------------------------------
# Instruction definitions
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class OpSpec:
    """Static description of one DSL opcode."""
    name: str          # DSL mnemonic, e.g. "ADD"
    pops: int          # stack values consumed
    pushes: int        # stack values produced (always 1)
    has_operand: bool  # True for PUSH_INPUT (index) and PUSH_CONST (value)
    wat: str           # corresponding WAT instruction (operand filled in later)


# The whitelist. 24 opcodes, none of which can trap.
OPSPECS: dict[str, OpSpec] = {
    "PUSH_INPUT": OpSpec("PUSH_INPUT", 0, 1, True, "local.get"),
    "PUSH_CONST": OpSpec("PUSH_CONST", 0, 1, True, "i32.const"),
    "ADD":   OpSpec("ADD",   2, 1, False, "i32.add"),
    "SUB":   OpSpec("SUB",   2, 1, False, "i32.sub"),
    "MUL":   OpSpec("MUL",   2, 1, False, "i32.mul"),
    "AND":   OpSpec("AND",   2, 1, False, "i32.and"),
    "OR":    OpSpec("OR",    2, 1, False, "i32.or"),
    "XOR":   OpSpec("XOR",   2, 1, False, "i32.xor"),
    "SHL":   OpSpec("SHL",   2, 1, False, "i32.shl"),
    "SHR_S": OpSpec("SHR_S", 2, 1, False, "i32.shr_s"),
    "SHR_U": OpSpec("SHR_U", 2, 1, False, "i32.shr_u"),
    "EQZ":   OpSpec("EQZ",   1, 1, False, "i32.eqz"),
    "EQ":    OpSpec("EQ",    2, 1, False, "i32.eq"),
    "NE":    OpSpec("NE",    2, 1, False, "i32.ne"),
    # --- V2 additions (still total / non-trapping) ---
    "ROTL":  OpSpec("ROTL",  2, 1, False, "i32.rotl"),
    "ROTR":  OpSpec("ROTR",  2, 1, False, "i32.rotr"),
    "LT_S":  OpSpec("LT_S",  2, 1, False, "i32.lt_s"),
    "LT_U":  OpSpec("LT_U",  2, 1, False, "i32.lt_u"),
    "GT_S":  OpSpec("GT_S",  2, 1, False, "i32.gt_s"),
    "GT_U":  OpSpec("GT_U",  2, 1, False, "i32.gt_u"),
    "LE_S":  OpSpec("LE_S",  2, 1, False, "i32.le_s"),
    "LE_U":  OpSpec("LE_U",  2, 1, False, "i32.le_u"),
    "GE_S":  OpSpec("GE_S",  2, 1, False, "i32.ge_s"),
    "GE_U":  OpSpec("GE_U",  2, 1, False, "i32.ge_u"),
}

# Reverse map WAT op -> DSL op (used by the translator).
WAT_TO_DSL: dict[str, str] = {spec.wat: name for name, spec in OPSPECS.items()}


@dataclass(frozen=True)
class Instr:
    """A single parsed DSL instruction."""
    op: str
    operand: int | None = None  # index for PUSH_INPUT, value for PUSH_CONST

    def __str__(self) -> str:
        if OPSPECS[self.op].has_operand:
            return f"{self.op} {self.operand}"
        return self.op


class DSLError(ValueError):
    """Raised for malformed, unsupported, or invalid DSL programs."""


# --------------------------------------------------------------------------
# Parser / serializer
# --------------------------------------------------------------------------
def parse(lines: list[str]) -> list[Instr]:
    """Parse a list of DSL text lines into Instr objects (no validation yet)."""
    program: list[Instr] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        op = parts[0].upper()
        if op not in OPSPECS:
            raise DSLError(f"Unknown opcode: {parts[0]!r}")
        spec = OPSPECS[op]
        if spec.has_operand:
            if len(parts) != 2:
                raise DSLError(f"{op} requires exactly one operand, got: {line!r}")
            try:
                operand = int(parts[1], 0)
            except ValueError:
                raise DSLError(f"{op} operand must be an integer: {parts[1]!r}")
            program.append(Instr(op, operand))
        else:
            if len(parts) != 1:
                raise DSLError(f"{op} takes no operand, got: {line!r}")
            program.append(Instr(op))
    return program


def serialize(program: list[Instr]) -> list[str]:
    """Turn a list of Instr back into text lines."""
    return [str(i) for i in program]


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def validate(
    program: list[Instr],
    num_inputs: int,
    *,
    max_len: int = 12,
    max_stack: int = 8,
) -> None:
    """
    Check a program is well-formed. Raises DSLError on any problem.

    Guarantees on success:
      - every opcode is on the whitelist,
      - PUSH_INPUT indices are in range [0, num_inputs),
      - the stack never underflows and never exceeds max_stack,
      - the program ends with exactly one value on the stack,
      - the program length is within max_len.
    """
    if len(program) > max_len:
        raise DSLError(f"Program too long: {len(program)} > {max_len}")
    if num_inputs < 1:
        raise DSLError("A program needs at least one input")

    depth = 0
    for idx, instr in enumerate(program):
        spec = OPSPECS[instr.op]
        if instr.op == "PUSH_INPUT":
            if instr.operand is None or not (0 <= instr.operand < num_inputs):
                raise DSLError(
                    f"PUSH_INPUT index {instr.operand} out of range "
                    f"[0, {num_inputs}) at position {idx}"
                )
        if depth < spec.pops:
            raise DSLError(
                f"Stack underflow at position {idx} ({instr.op}): "
                f"needs {spec.pops}, have {depth}"
            )
        depth = depth - spec.pops + spec.pushes
        if depth > max_stack:
            raise DSLError(
                f"Stack depth {depth} exceeds max {max_stack} at position {idx}"
            )
    if depth != 1:
        raise DSLError(f"Program must leave exactly one result, left {depth}")


# --------------------------------------------------------------------------
# Reference interpreter (concrete 32-bit semantics)
# --------------------------------------------------------------------------
# Each binary op takes two unsigned 32-bit ints and returns an unsigned 32-bit
# int. The second operand popped is the top of stack (right-hand side), matching
# WASM stack order: for "a b SUB" the result is a - b.
_BINOPS: dict[str, Callable[[int, int], int]] = {
    "ADD":   lambda a, b: to_u32(a + b),
    "SUB":   lambda a, b: to_u32(a - b),
    "MUL":   lambda a, b: to_u32(a * b),
    "AND":   lambda a, b: a & b,
    "OR":    lambda a, b: a | b,
    "XOR":   lambda a, b: a ^ b,
    # Shift count is taken mod 32 (WASM semantics).
    "SHL":   lambda a, b: to_u32(a << (b & 31)),
    "SHR_U": lambda a, b: a >> (b & 31),
    "SHR_S": lambda a, b: to_u32(to_s32(a) >> (b & 31)),
    "EQ":    lambda a, b: 1 if a == b else 0,
    "NE":    lambda a, b: 1 if a != b else 0,
    # --- V2: rotates (count mod 32) and ordered comparisons ---
    "ROTL":  lambda a, b: _rotl(a, b & 31),
    "ROTR":  lambda a, b: _rotr(a, b & 31),
    "LT_S":  lambda a, b: 1 if to_s32(a) < to_s32(b) else 0,
    "LT_U":  lambda a, b: 1 if a < b else 0,
    "GT_S":  lambda a, b: 1 if to_s32(a) > to_s32(b) else 0,
    "GT_U":  lambda a, b: 1 if a > b else 0,
    "LE_S":  lambda a, b: 1 if to_s32(a) <= to_s32(b) else 0,
    "LE_U":  lambda a, b: 1 if a <= b else 0,
    "GE_S":  lambda a, b: 1 if to_s32(a) >= to_s32(b) else 0,
    "GE_U":  lambda a, b: 1 if a >= b else 0,
}


def _rotl(x: int, k: int) -> int:
    x &= MASK32
    return ((x << k) | (x >> (32 - k))) & MASK32 if k else x


def _rotr(x: int, k: int) -> int:
    x &= MASK32
    return ((x >> k) | (x << (32 - k))) & MASK32 if k else x


def interpret(program: list[Instr], inputs: list[int]) -> int:
    """
    Execute a validated program on concrete inputs and return the i32 result
    as an unsigned 32-bit value. `inputs` are given as Python ints (any sign);
    they are reduced to 32 bits on load.
    """
    stack: list[int] = []
    for instr in program:
        op = instr.op
        if op == "PUSH_INPUT":
            stack.append(to_u32(inputs[instr.operand]))
        elif op == "PUSH_CONST":
            stack.append(to_u32(instr.operand))
        elif op == "EQZ":
            a = stack.pop()
            stack.append(1 if a == 0 else 0)
        else:  # binary op
            b = stack.pop()
            a = stack.pop()
            stack.append(_BINOPS[op](a, b))
    return stack[-1]


def instruction_count(program: list[Instr]) -> int:
    """Cost metric: number of instructions."""
    return len(program)
