"""
Integration test for the full pipeline on the x+0 example. Requires WABT and
Wasmtime; skipped otherwise. Uses an isolated rules file via the distiller's
default path override is not available here, so this test runs the block-level
optimizer directly to avoid touching the project rules.json.
"""
import shutil

import pytest

from src.dsl import parse, serialize
from src.pipeline import optimize_block, load_config
from src import distiller

needs_tools = pytest.mark.skipif(
    shutil.which("wat2wasm") is None, reason="wat2wasm not on PATH"
)


@needs_tools
def test_optimize_x_plus_zero(tmp_path, monkeypatch):
    # Redirect the rule store to a temp file so the test is isolated.
    rules_file = tmp_path / "rules.json"
    rules_file.write_text("[]")
    monkeypatch.setattr(distiller, "DEFAULT_RULES_PATH", rules_file)

    cfg = load_config()
    cfg["differential_tests"] = 200
    prog = parse(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"])
    result = optimize_block(prog, 1, cfg, allow_gemini=False, verbose=False)

    assert result["accepted"]
    assert result["rewrite"] == ["PUSH_INPUT 0"]
    assert result["original_instr"] == 3 and result["rewrite_instr"] == 1
    assert result["z3"] == "unsat"
