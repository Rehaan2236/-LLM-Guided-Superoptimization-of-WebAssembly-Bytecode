"""
verifier.py — Z3 SMT equivalence verification.

This is the correctness backbone. Given two DSL programs over the same inputs,
it proves (or disproves) that they compute the same i32 result for EVERY
possible input, under our modeled 32-bit semantics.

How it works:
  1. Create one 32-bit Z3 BitVec per input (shared by both programs).
  2. Symbolically execute each program over a Z3 stack, building a bitvector
     expression for the single output value.
  3. Ask Z3 whether there EXISTS an input where the outputs differ:
        exists inputs. original_out != candidate_out
  4. Interpret the answer:
        unsat   -> no differing input exists  -> EQUIVALENT  -> accept
        sat     -> a counterexample exists     -> NOT EQUIV   -> reject
        unknown -> timeout / gave up           -> UNVERIFIED  -> reject

Why bitvectors? WASM i32 arithmetic is modular 2**32 two's-complement.
Z3's BitVec(32) models exactly that: +, -, * wrap automatically, and the
signed/unsigned distinction is carried by the operation we choose
(e.g. >> arithmetic vs logical), not by the value.

Shift semantics: WASM masks the shift count to 5 bits (count mod 32). We
replicate that by masking the shift-amount operand with & 31 before shifting.
This is the single most common place a naive encoding goes wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

from z3 import (
    BitVec, BitVecRef, Solver, If,
    LShR, UDiv,  # UDiv imported for completeness; not used in V1
    sat, unsat,
)

from src.dsl import Instr, OPSPECS


@dataclass
class VerifyResult:
    """Outcome of an equivalence check."""
    status: str                      # "EQUIVALENT" | "NOT_EQUIVALENT" | "UNVERIFIED"
    accepted: bool                   # True only for EQUIVALENT
    z3_result: str                   # "unsat" | "sat" | "unknown"
    counterexample: dict | None = None  # {input_i: signed_value, ...} when sat
    reason: str = ""


def _symbolic_eval(program: list[Instr], inputs: list[BitVecRef]) -> BitVecRef:
    """Symbolically execute a DSL program, returning the output bitvector."""
    stack: list[BitVecRef] = []
    for instr in program:
        op = instr.op
        if op == "PUSH_INPUT":
            stack.append(inputs[instr.operand])
        elif op == "PUSH_CONST":
            # BitVecVal via Python int; mask to 32 bits for safety.
            from z3 import BitVecVal
            stack.append(BitVecVal(instr.operand & 0xFFFFFFFF, 32))
        elif op == "EQZ":
            a = stack.pop()
            stack.append(If(a == 0, _one(), _zero()))
        else:
            b = stack.pop()
            a = stack.pop()
            stack.append(_binop(op, a, b))
    return stack[-1]


def _one():
    from z3 import BitVecVal
    return BitVecVal(1, 32)


def _zero():
    from z3 import BitVecVal
    return BitVecVal(0, 32)


def _binop(op: str, a: BitVecRef, b: BitVecRef) -> BitVecRef:
    """Build the Z3 expression for a binary DSL op with WASM i32 semantics."""
    if op == "ADD":
        return a + b
    if op == "SUB":
        return a - b
    if op == "MUL":
        return a * b
    if op == "AND":
        return a & b
    if op == "OR":
        return a | b
    if op == "XOR":
        return a ^ b
    if op == "SHL":
        return a << (b & 31)           # count mod 32
    if op == "SHR_S":
        return a >> (b & 31)           # ">>" on a BitVec is ARITHMETIC (signed)
    if op == "SHR_U":
        return LShR(a, b & 31)         # logical (unsigned) shift right
    if op == "EQ":
        return If(a == b, _one(), _zero())
    if op == "NE":
        return If(a != b, _one(), _zero())
    # --- V2 ---
    if op == "ROTL":
        from z3 import RotateLeft
        return RotateLeft(a, b)        # z3 rotate uses count mod width (matches WASM)
    if op == "ROTR":
        from z3 import RotateRight
        return RotateRight(a, b)
    if op == "LT_S":
        return If(a < b, _one(), _zero())       # "<" on BitVec is SIGNED in z3
    if op == "LT_U":
        from z3 import ULT
        return If(ULT(a, b), _one(), _zero())
    if op == "GT_S":
        return If(a > b, _one(), _zero())
    if op == "GT_U":
        from z3 import UGT
        return If(UGT(a, b), _one(), _zero())
    if op == "LE_S":
        return If(a <= b, _one(), _zero())
    if op == "LE_U":
        from z3 import ULE
        return If(ULE(a, b), _one(), _zero())
    if op == "GE_S":
        return If(a >= b, _one(), _zero())
    if op == "GE_U":
        from z3 import UGE
        return If(UGE(a, b), _one(), _zero())
    raise ValueError(f"Unsupported op in verifier: {op}")


def verify(
    original: list[Instr],
    candidate: list[Instr],
    num_inputs: int,
    *,
    timeout_ms: int = 5000,
) -> VerifyResult:
    """
    Prove whether `original` and `candidate` are equivalent for all inputs.

    Both programs must be pre-validated (see dsl.validate). `num_inputs` is the
    number of shared i32 parameters.
    """
    inputs = [BitVec(f"in{i}", 32) for i in range(num_inputs)]
    out_o = _symbolic_eval(original, inputs)
    out_c = _symbolic_eval(candidate, inputs)

    solver = Solver()
    solver.set("timeout", timeout_ms)
    solver.add(out_o != out_c)        # look for ANY distinguishing input
    result = solver.check()

    if result == unsat:
        return VerifyResult(
            status="EQUIVALENT", accepted=True, z3_result="unsat",
            reason="No input distinguishes the two programs.",
        )
    if result == sat:
        model = solver.model()
        ce = {}
        for i, bv in enumerate(inputs):
            val = model.eval(bv, model_completion=True).as_long()
            # present as signed for readability
            ce[f"in{i}"] = val - (1 << 32) if val & 0x80000000 else val
        return VerifyResult(
            status="NOT_EQUIVALENT", accepted=False, z3_result="sat",
            counterexample=ce,
            reason="Found an input where the two programs differ.",
        )
    return VerifyResult(
        status="UNVERIFIED", accepted=False, z3_result="unknown",
        reason="Z3 returned unknown (timeout). Conservatively rejected.",
    )


if __name__ == "__main__":
    # Quick self-check (run: python -m src.verifier)
    from src.dsl import parse

    cases = [
        ("x+0 vs x", ["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"], ["PUSH_INPUT 0"], 1, "EQUIVALENT"),
        ("x+1 vs x", ["PUSH_INPUT 0", "PUSH_CONST 1", "ADD"], ["PUSH_INPUT 0"], 1, "NOT_EQUIVALENT"),
        ("x*2 vs x+x", ["PUSH_INPUT 0", "PUSH_CONST 2", "MUL"],
         ["PUSH_INPUT 0", "PUSH_INPUT 0", "ADD"], 1, "EQUIVALENT"),
        ("x+1 vs x+2", ["PUSH_INPUT 0", "PUSH_CONST 1", "ADD"],
         ["PUSH_INPUT 0", "PUSH_CONST 2", "ADD"], 1, "NOT_EQUIVALENT"),
        ("x<<40 vs x<<8", ["PUSH_INPUT 0", "PUSH_CONST 40", "SHL"],
         ["PUSH_INPUT 0", "PUSH_CONST 8", "SHL"], 1, "EQUIVALENT"),
    ]
    for name, o, c, n, expect in cases:
        r = verify(parse(o), parse(c), n)
        ok = "OK" if r.status == expect else "WRONG"
        extra = f" ce={r.counterexample}" if r.counterexample else ""
        print(f"[{ok}] {name}: {r.status} ({r.z3_result}){extra}")
