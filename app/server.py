"""
server.py — local web front end for the WASM superoptimizer.

Pure standard library (no new dependencies). It wraps the existing pipeline:
nothing is re-implemented here, every verdict comes from src/*.

Run from the project root:
    python -m app.server            # http://127.0.0.1:8000
    python -m app.server --port 9000
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
EXAMPLES = ROOT / "examples"

# Pick up GEMINI_API_KEY from .env the same way the proposer does (best effort).
_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from src import pipeline  # noqa: E402
from src.dsl import serialize, instruction_count  # noqa: E402
from src.extractor import extract_blocks  # noqa: E402
from src.wasm_translator import wasm_instrs_to_dsl, dsl_to_wat_module  # noqa: E402
from src.dsl import parse as dsl_parse  # noqa: E402
from src.baseline import run_wasm_opt  # noqa: E402
from src.distiller import load_rules  # noqa: E402

# optimize_block prints to stdout, so runs are serialized.
_run_lock = threading.Lock()


def _tool(name: str) -> str | None:
    return shutil.which(name)


def status() -> dict:
    return {
        "wat2wasm": bool(_tool("wat2wasm")),
        "wasm2wat": bool(_tool("wasm2wat")),
        "wasm_opt": bool(_tool("wasm-opt")),
        "gemini_key": bool(os.environ.get("GEMINI_API_KEY")),
        "gemini_model": pipeline.load_config().get("gemini_model"),
        "rules": len(load_rules()),
    }


def list_examples() -> list[dict]:
    out = []
    for wat in sorted(EXAMPLES.glob("*.wat")):
        out.append({"name": wat.stem, "wat": wat.read_text()})
    return out


def _wat_to_wasm(wat: str, out: Path) -> None:
    exe = _tool("wat2wasm")
    if not exe:
        raise RuntimeError("wat2wasm not found on PATH (install WABT).")
    src = out.with_suffix(".wat")
    src.write_text(wat)
    p = subprocess.run([exe, str(src), "-o", str(out)], capture_output=True, text=True)
    if p.returncode != 0:
        raise ValueError("wat2wasm rejected the input:\n" + (p.stderr or p.stdout).strip())


def _wasm_to_wat(wasm: Path) -> str:
    exe = _tool("wasm2wat")
    if not exe:
        return ""
    p = subprocess.run([exe, str(wasm)], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


# Log line -> stage number. Lines before a new "[n]" marker belong to that stage.
_STAGE_RE = re.compile(r"^\s*\[(\d+)\]\s*(.*)")
STAGE_TITLES = {
    1: "Extract basic block",
    2: "Translate to DSL",
    3: "Check stored rules",
    4: "Generate candidates",
    5: "Validate candidate",
    6: "Differential testing",
    7: "Z3 verification",
    8: "Decision",
    9: "Instruction count",
    10: "Distil rule",
}


def parse_trace(log: str) -> list[dict]:
    """Group the pipeline's printed trace into ordered stage entries."""
    stages: list[dict] = []
    for raw in log.splitlines():
        if not raw.strip() or set(raw.strip()) == {"="} or raw.strip() == "WASM SUPEROPTIMIZER":
            continue
        m = _STAGE_RE.match(raw)
        if m:
            n = int(m.group(1))
            stages.append({"n": n, "title": STAGE_TITLES.get(n, f"Stage {n}"),
                           "headline": m.group(2).strip(), "lines": []})
        elif stages:
            stages[-1]["lines"].append(raw.rstrip())
        else:
            stages.append({"n": 0, "title": "Start", "headline": "", "lines": [raw.rstrip()]})
    for s in stages:
        text = "\n".join(s["lines"])
        if "[FAIL]" in text or "[INVALID]" in text or "[REJECT]" in text:
            s["status"] = "fail"
        elif "[PASS]" in text or "[OK]" in text or "ACCEPTED" in s["headline"] \
                or "UNSAT" in text or "[RULE HIT]" in text:
            s["status"] = "pass"
        elif "[NONE]" in text or "No candidate verified" in s["headline"]:
            s["status"] = "warn"
        else:
            s["status"] = "info"
    return stages


def optimize(payload: dict) -> dict:
    t0 = time.time()
    use_gemini = bool(payload.get("use_gemini", False))
    fn_index = payload.get("function")

    with tempfile.TemporaryDirectory() as d:
        wasm = Path(d) / "input.wasm"
        if payload.get("wasm_b64"):
            wasm.write_bytes(base64.b64decode(payload["wasm_b64"]))
        elif payload.get("wat", "").strip():
            _wat_to_wasm(payload["wat"], wasm)
        else:
            raise ValueError("Provide WAT text or a .wasm file.")

        blocks = extract_blocks(wasm)
        functions = [{
            "function": b["function"], "params": b["params"],
            "skipped": b["skipped"], "reason": b.get("reason", ""),
            "instructions": b.get("instructions", []),
        } for b in blocks]
        supported = [b for b in blocks if not b["skipped"]]
        if not supported:
            return {"ok": False, "error": "No supported function found.",
                    "detail": "V1 handles straight-line i32 functions only "
                              "(i32 params, one i32 result, no branches/memory/calls).",
                    "functions": functions}

        block = next((b for b in supported if b["function"] == fn_index), supported[0])
        num_inputs = block["params"]
        program = wasm_instrs_to_dsl(block["instructions"])
        cfg = pipeline.load_config()
        if payload.get("bypass_cache"):
            cfg["cache_enabled"] = False  # force a live Gemini call

        buf = io.StringIO()
        with _run_lock, contextlib.redirect_stdout(buf):
            print("[1] Extracting basic block...")
            print(f"    [OK] {len(block['instructions'])} instr, {num_inputs} input(s)")
            print("\n[2] Translating to DSL...")
            print(f"    [OK] {serialize(program)}\n")
            allow = use_gemini and bool(os.environ.get("GEMINI_API_KEY"))
            result = pipeline.optimize_block(program, num_inputs, cfg, allow_gemini=allow)
        log = buf.getvalue()

        original_wat = dsl_to_wat_module(program, num_inputs)
        rewrite_wat = None
        if result["accepted"] and result["rewrite"]:
            rewrite_wat = dsl_to_wat_module(dsl_parse(result["rewrite"]), num_inputs)

        # Industry baseline: Binaryen wasm-opt -Oz on the same input.
        baseline = None
        if _tool("wasm-opt"):
            try:
                opt = Path(d) / "opt.wasm"
                run_wasm_opt(wasm, opt)
                ob = extract_blocks(opt)
                match = next((b for b in ob if b["function"] == block["function"]
                              and not b["skipped"]), None)
                baseline = {
                    "instr": len(match["instructions"]) if match else None,
                    "bytes": opt.stat().st_size,
                    "input_bytes": wasm.stat().st_size,
                }
            except Exception as e:  # baseline is informational only
                baseline = {"error": str(e)}

    return {
        "ok": True,
        "function": block["function"],
        "functions": functions,
        "num_inputs": num_inputs,
        "original_dsl": result["original"],
        "original_wat": original_wat,
        "result": result,
        "rewrite_wat": rewrite_wat,
        "baseline": baseline,
        "gemini_used": use_gemini and bool(os.environ.get("GEMINI_API_KEY")),
        "gemini_requested_without_key": use_gemini and not os.environ.get("GEMINI_API_KEY"),
        "stages": parse_trace(log),
        "log": log,
        "seconds": round(time.time() - t0, 2),
    }


def reset_rules() -> dict:
    (ROOT / "rules" / "rules.json").write_text("[]\n")
    return {"ok": True, "rules": 0}


class Handler(BaseHTTPRequestHandler):
    server_version = "WasmSuperopt/1.0"

    def log_message(self, fmt, *args):  # quieter console
        print("  ", fmt % args)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/status":
            self._json(200, status())
        elif path == "/api/examples":
            self._json(200, list_examples())
        elif path == "/api/rules":
            self._json(200, load_rules())
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"ok": False, "error": "Invalid JSON body."})
        try:
            if self.path == "/api/optimize":
                self._json(200, optimize(payload))
            elif self.path == "/api/rules/reset":
                self._json(200, reset_rules())
            else:
                self._json(404, {"error": "not found"})
        except ValueError as e:
            self._json(200, {"ok": False, "error": "Invalid input", "detail": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._json(200, {"ok": False, "error": type(e).__name__, "detail": str(e)})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"WASM Superoptimizer UI -> http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
