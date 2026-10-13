"""
extractor.py — basic-block extraction from a .wasm file.

Simplification: a "basic block" is the entire body of a function whose
signature is (param i32...) (result i32) and whose body uses only whitelisted
straight-line instructions. Because V1 has no branches, the function body IS a
single basic block.

Pipeline:
  sample.wasm --(wasm2wat)--> WAT text --(parse)--> list of block dicts (JSON)

A function is reported as "skipped" (with a reason) if it:
  - has a non-i32 parameter or result,
  - returns something other than exactly one i32,
  - uses any instruction outside the V1 whitelist (control flow, memory, etc.).

We never silently process unsupported code.

Requires `wasm2wat` (WABT) on PATH.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from src.dsl import WAT_TO_DSL


class ExtractorError(RuntimeError):
    pass


def _run_wasm2wat(wasm_path: Path) -> str:
    exe = shutil.which("wasm2wat")
    if exe is None:
        raise ExtractorError(
            "wasm2wat not found on PATH. Install WABT and add its bin/ to PATH."
        )
    proc = subprocess.run(
        [exe, str(wasm_path)], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise ExtractorError(f"wasm2wat failed:\n{proc.stderr}")
    return proc.stdout


# Supported bare (operand-less) WAT ops are exactly the keys of WAT_TO_DSL
# minus the two that carry operands.
_BARE_OPS = {wat for wat in WAT_TO_DSL if wat not in ("local.get", "i32.const")}


def _parse_wat_functions(wat: str) -> list[dict]:
    """
    Parse WAT text into a list of block dicts.

    This is a light, line-oriented parser tuned to wasm2wat's output for the V1
    instruction set. It is NOT a general WAT parser; it handles straight-line
    i32 functions and flags anything else as skipped.
    """
    blocks: list[dict] = []
    lines = wat.splitlines()

    func_idx = -1
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if line.startswith("(func"):
            func_idx += 1
            # Collect the full function text (until the matching close at the
            # start-of-func paren depth). wasm2wat formats one instr per line,
            # so a simple paren-depth scan over the remaining lines works.
            func_lines = [line]
            depth = line.count("(") - line.count(")")
            j = i + 1
            while j < n and depth > 0:
                func_lines.append(lines[j].strip())
                depth += lines[j].count("(") - lines[j].count(")")
                j += 1
            blocks.append(_parse_one_func(func_idx, func_lines))
            i = j
        else:
            i += 1
    return blocks


def _parse_one_func(func_idx: int, func_lines: list[str]) -> dict:
    header = func_lines[0]

    # Count i32 params. wasm2wat emits e.g. "(param i32 i32)" possibly with
    # names like "(param $x i32)". Count i32 tokens inside param groups.
    params = 0
    bad_param = False
    for m in re.finditer(r"\(param([^)]*)\)", header):
        toks = m.group(1).split()
        for t in toks:
            if t == "i32":
                params += 1
            elif not t.startswith("$"):
                bad_param = True  # non-i32 type token

    result_match = re.search(r"\(result([^)]*)\)", header)
    result_type = result_match.group(1).strip() if result_match else ""

    base = {"function": func_idx, "params": params, "result": result_type}

    if bad_param or (result_type and result_type != "i32"):
        return {**base, "skipped": True,
                "reason": "non-i32 parameter or result type"}
    if result_type != "i32":
        return {**base, "skipped": True,
                "reason": "function does not return exactly one i32"}

    instructions: list[dict] = []
    for raw in func_lines[1:]:
        line = raw.strip().rstrip(")").strip()
        if not line or line.startswith("(param") or line.startswith("(result") \
                or line.startswith("(local") or line.startswith("(export") \
                or line.startswith("(type"):
            continue
        if not line:
            continue
        parts = line.split()
        op = parts[0]
        if op == "local.get":
            instructions.append({"op": "local.get", "index": int(parts[1])})
        elif op == "i32.const":
            instructions.append({"op": "i32.const", "value": int(parts[1], 0)})
        elif op in _BARE_OPS:
            instructions.append({"op": op})
        elif op in ("", "func"):
            continue
        else:
            return {**base, "skipped": True,
                    "reason": f"unsupported instruction: {op}"}

    if not instructions:
        return {**base, "skipped": True, "reason": "empty function body"}

    return {**base, "skipped": False, "instructions": instructions}


def extract_blocks(wasm_path: str | Path) -> list[dict]:
    """Extract all candidate basic blocks from a .wasm file."""
    wasm_path = Path(wasm_path)
    if not wasm_path.exists():
        raise ExtractorError(f"File not found: {wasm_path}")
    wat = _run_wasm2wat(wasm_path)
    return _parse_wat_functions(wat)


def first_supported_block(wasm_path: str | Path) -> dict:
    """Return the first non-skipped block, or raise if none."""
    for b in extract_blocks(wasm_path):
        if not b.get("skipped"):
            return b
    raise ExtractorError(f"No supported straight-line i32 block in {wasm_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m src.extractor <file.wasm>")
        sys.exit(1)
    out = extract_blocks(sys.argv[1])
    print(json.dumps(out, indent=2))
