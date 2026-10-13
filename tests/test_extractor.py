"""
Integration tests for the extractor. These require WABT (wat2wasm/wasm2wat)
on PATH. They are skipped automatically if the tools or compiled examples are
missing, so the unit suite still runs on a bare machine.
"""
import shutil
from pathlib import Path

import pytest

from src.extractor import extract_blocks

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
needs_wabt = pytest.mark.skipif(
    shutil.which("wasm2wat") is None, reason="wasm2wat not on PATH"
)


@needs_wabt
def test_extract_x_plus_zero():
    wasm = EXAMPLES / "x_plus_zero.wasm"
    if not wasm.exists():
        pytest.skip("examples not compiled yet (run scripts/compile_examples.ps1)")
    blocks = extract_blocks(wasm)
    assert len(blocks) == 1
    b = blocks[0]
    assert not b["skipped"]
    assert b["params"] == 1 and b["result"] == "i32"
    ops = [i["op"] for i in b["instructions"]]
    assert ops == ["local.get", "i32.const", "i32.add"]


@needs_wabt
def test_extract_x_times_two():
    wasm = EXAMPLES / "x_times_two.wasm"
    if not wasm.exists():
        pytest.skip("examples not compiled yet")
    b = extract_blocks(wasm)[0]
    assert not b["skipped"]
    assert [i["op"] for i in b["instructions"]] == \
        ["local.get", "i32.const", "i32.mul"]
