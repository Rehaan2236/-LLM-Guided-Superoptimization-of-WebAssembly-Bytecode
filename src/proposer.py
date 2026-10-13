"""
proposer.py — LLM candidate generation (Gemini) with a manual fallback.

The proposer takes a DSL block and asks Gemini for SHORTER, equivalent candidate
rewrites. It returns a list of candidate DSL programs. It never decides anything:
every candidate it returns must still pass DSL validation, differential testing,
and Z3 before the pipeline accepts it.

Hard requirements honoured here:
  - Uses the CURRENT google-genai SDK (not the deprecated google-generativeai).
  - Forces strict JSON output and parses it defensively.
  - Retries on malformed JSON.
  - Deduplicates candidates.
  - Caches responses on disk keyed by the block + model, so repeated runs and the
    demo do not burn API quota.
  - Degrades gracefully: if the API key is missing or the call fails, it falls
    back to built-in heuristic candidates so the correctness pipeline still runs.
  - Never prints the API key.

The API key is read from the GEMINI_API_KEY environment variable by the SDK.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from src.dsl import Instr, parse, serialize, DSLError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "cache"
ENV_FILE = PROJECT_ROOT / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """
    Read KEY=VALUE lines from the project's .env file into os.environ, so the
    API key never has to be set in the terminal. Blank lines and # comments are
    ignored, and surrounding quotes are stripped. A variable already set in
    the environment is NOT overwritten. The key's value is never printed.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


# Load .env as soon as this module is imported (the pipeline imports it before
# deciding whether Gemini is available).
load_env_file()

PROMPT_VERSION = "v2"  # bump when the prompt changes, so stale cached answers are ignored

PROMPT_TEMPLATE = """\
You are a superoptimizer for a tiny stack-based DSL that models 32-bit
WebAssembly integer code. Find a SHORTER program with the SAME output for EVERY
possible input.

SEMANTICS (all values are 32-bit; arithmetic wraps modulo 2^32):
  PUSH_INPUT k   push input number k
  PUSH_CONST c   push the 32-bit constant c
Binary ops: pop b (top), then pop a, then push the result of (a OP b):
  ADD a+b   SUB a-b   MUL a*b   AND a&b   OR a|b   XOR a^b
  SHL  a << (b mod 32)
  SHR_S  arithmetic (sign-filling) right shift by (b mod 32)
  SHR_U  logical (zero-filling) right shift by (b mod 32)
  ROTL / ROTR  rotate left / right by (b mod 32)
  EQ   1 if a == b else 0          NE   1 if a != b else 0
  LT_S LE_S GT_S GE_S   signed comparisons of a and b, result 1 or 0
  LT_U LE_U GT_U GE_U   unsigned comparisons of a and b, result 1 or 0
Unary op:
  EQZ  pop a; push 1 if a == 0 else 0      (EQZ means "is zero", NOT "not equal")

PROGRAM ({num_inputs} input(s), {length} instructions):
{block}
{feedback}
Before answering, check every candidate on concrete inputs such as 0, 1, -1,
x == y, x != y. Only include candidates that you have checked.

Propose up to {k} candidates. Each must be strictly shorter than {length}
instructions, use only the opcodes above, and leave exactly one value on the stack.

Reply with JSON only, in exactly this shape:
{{"candidates": [{{"instructions": ["PUSH_INPUT 0"], "reason": "x + 0 = x"}}]}}
"""

FEEDBACK_TEMPLATE = """
YOUR EARLIER CANDIDATES WERE WRONG. Do not propose them again:
{items}
"""


@dataclass
class Candidate:
    program: list[Instr]
    reason: str
    source: str  # "gemini" | "manual" | "cache"


def _block_key(block_lines: list[str], num_inputs: int, model: str) -> str:
    h = hashlib.sha256()
    h.update(("|".join(block_lines) + f"|n={num_inputs}|m={model}").encode())
    return h.hexdigest()[:16]


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _load_cache(key: str) -> list[dict] | None:
    p = _cache_path(key)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None
    return None


def _save_cache(key: str, raw_candidates: list[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(key).write_text(json.dumps(raw_candidates, indent=2))


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response and parse it."""
    text = text.strip()
    # strip ```json ... ``` fences if present
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    return json.loads(text)


def _manual_candidates(program: list[Instr], num_inputs: int) -> list[Candidate]:
    """
    Deterministic, offline heuristic candidates. These let the pipeline and the
    demo run with no network/API. They are still only PROPOSALS and must pass
    differential testing + Z3 before acceptance.
    """
    out: list[Candidate] = []
    lines = serialize(program)

    def add(instr_lines: list[str], reason: str):
        try:
            out.append(Candidate(parse(instr_lines), reason, "manual"))
        except DSLError:
            pass

    # x OP 0 / x OP 1 identities on a 3-instruction block: PUSH_INPUT k, PUSH_CONST c, OP
    if len(program) == 3 and program[0].op == "PUSH_INPUT" \
            and program[1].op == "PUSH_CONST":
        k = program[0].operand
        c = program[1].operand
        op = program[2].op
        if (op, c) in {("ADD", 0), ("SUB", 0), ("OR", 0), ("XOR", 0),
                       ("MUL", 1), ("SHL", 0), ("SHR_S", 0), ("SHR_U", 0)}:
            add([f"PUSH_INPUT {k}"], f"identity: x {op} {c} = x")
        if op == "MUL" and c == 0:
            add(["PUSH_CONST 0"], "x * 0 = 0")
        if op == "AND" and c == 0:
            add(["PUSH_CONST 0"], "x & 0 = 0")
        if op == "MUL" and c == 2:
            add([f"PUSH_INPUT {k}", f"PUSH_INPUT {k}", "ADD"], "x * 2 = x + x")

    # --- V2 novelty rewrites discovered by the enumerative search (verified by
    #     superopt_search, re-proved live by Z3 before acceptance). These are
    #     rewrites wasm-opt -Oz does NOT perform. ---
    # (x ^ y) == 0  ->  x == y
    if lines == ["PUSH_INPUT 0", "PUSH_INPUT 1", "XOR", "EQZ"]:
        add(["PUSH_INPUT 0", "PUSH_INPUT 1", "EQ"], "(x ^ y) == 0  <=>  x == y")
    # (x >>s 31) == 0  ->  x >=s 0   (sign-bit test)
    if lines == ["PUSH_INPUT 0", "PUSH_CONST 31", "SHR_S", "EQZ"]:
        add(["PUSH_INPUT 0", "PUSH_CONST 0", "GE_S"], "sign-bit test: (x>>31)==0 <=> x>=0")
    # (x >>u 31) == 0  ->  x >=s 0
    if lines == ["PUSH_INPUT 0", "PUSH_CONST 31", "SHR_U", "EQZ"]:
        add(["PUSH_INPUT 0", "PUSH_CONST 0", "GE_S"], "sign-bit test: (x>>>31)==0 <=> x>=0")

    return out


def _format_feedback(feedback: list[dict] | None) -> str:
    if not feedback:
        return ""
    items = []
    for f in feedback:
        items.append(
            f"  - {' ; '.join(f['instructions'])}  is wrong: on inputs "
            f"{f['inputs']} the original gives {f['original']} but this gives "
            f"{f['candidate']}"
        )
    return FEEDBACK_TEMPLATE.format(items="\n".join(items))


_BUSY_MARKERS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "overloaded",
                 "high demand", "timed out", "Timeout")
_GONE_MARKERS = ("404", "NOT_FOUND", "no longer available")


def _gemini_candidates(
    block_lines: list[str], num_inputs: int, model: str, k: int, retries: int,
    feedback: list[dict] | None = None,
    thinking_level: str = "low", timeout_s: int = 90,
    fallback_models: list[str] | None = None,
) -> list[dict] | None:
    """
    Call Gemini and return raw candidate dicts, or None if every model fails.

    Resilience:
      - Busy errors (503 high demand, 429 rate limit, timeout) are retried on
        the same model after a short wait (2s, then 6s).
      - If a model stays busy, or doesn't exist for this key (404), the next
        model in `fallback_models` is tried.
      - Malformed JSON is re-asked; anything else stops and falls back.
    The model that actually answered is printed.
    """
    if not os.environ.get("GEMINI_API_KEY"):
        return None
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None
    import time

    prompt = PROMPT_TEMPLATE.format(
        num_inputs=num_inputs, length=len(block_lines),
        block="\n".join(block_lines), k=k, feedback=_format_feedback(feedback),
    )

    def make_config(with_thinking: bool):
        kwargs = dict(
            response_mime_type="application/json",
            temperature=0.2,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if with_thinking and thinking_level:
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=thinking_level.upper())
        return types.GenerateContentConfig(**kwargs)

    try:
        client = genai.Client(http_options=types.HttpOptions(timeout=timeout_s * 1000))
    except Exception:
        return None

    models = [model] + [m for m in (fallback_models or []) if m and m != model]
    waits = [2, 6]                       # pauses between busy-retries on one model
    for mi, m in enumerate(models):
        if mi > 0:
            print(f"    switching to fallback model {m}...", flush=True)
        else:
            print(f"    waiting for Gemini ({m}, thinking={thinking_level or 'default'})...",
                  flush=True)
        with_thinking = True
        busy_tries = 0
        json_tries = 0
        t0 = time.time()
        while True:
            try:
                resp = client.models.generate_content(
                    model=m, contents=prompt, config=make_config(with_thinking))
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                if with_thinking and thinking_level and "think" in msg.lower():
                    with_thinking = False                  # model rejects the setting
                    continue
                if any(x in msg for x in _GONE_MARKERS):
                    print(f"    {m}: not available for this key")
                    break                                  # next model
                if any(x.lower() in msg.lower() for x in _BUSY_MARKERS):
                    if busy_tries < len(waits):
                        print(f"    {m} is busy (high demand), retrying in "
                              f"{waits[busy_tries]}s...", flush=True)
                        time.sleep(waits[busy_tries])
                        busy_tries += 1
                        continue
                    print(f"    {m} still busy")
                    break                                  # next model
                print(f"[proposer] Gemini call failed: {msg[:300]}")
                break                                      # next model
            try:
                data = _extract_json(resp.text)
                cands = data.get("candidates", [])
                if isinstance(cands, list):
                    print(f"    {m} replied in {time.time() - t0:.1f}s")
                    return cands
            except Exception:
                pass
            json_tries += 1
            if json_tries >= retries:
                print(f"    {m}: malformed JSON after {retries} tries")
                break
    print("[proposer] all Gemini models failed")
    return None


def gemini_available() -> bool:
    """True if a key is configured and the SDK is installed."""
    if not os.environ.get("GEMINI_API_KEY"):
        return False
    try:
        import google.genai  # noqa: F401
    except ImportError:
        return False
    return True


def propose(
    program: list[Instr],
    num_inputs: int,
    *,
    model: str = "gemini-3.5-flash",
    k: int = 4,
    retries: int = 3,
    use_cache: bool = True,
    allow_gemini: bool = True,
    feedback: list[dict] | None = None,
    thinking_level: str = "low",
    timeout_s: int = 90,
    fallback_models: list[str] | None = None,
) -> list[Candidate]:
    """
    Return a deduplicated list of candidate rewrites for `program`.

    If Gemini is available, candidates come ONLY from Gemini (or from the cache
    of an earlier Gemini answer). The offline heuristic list is used only when
    Gemini is unavailable or the call fails, and those candidates are labelled
    "manual" so the output always shows where a candidate came from.

    `feedback` lists earlier wrong candidates with their counterexamples; it is
    included in the prompt so Gemini can correct itself (and bypasses the cache).
    """
    block_lines = serialize(program)
    key = _block_key(block_lines, num_inputs, model + "|" + PROMPT_VERSION)

    raw: list[dict] | None = None
    source = "manual"
    if use_cache and not feedback:
        raw = _load_cache(key)
        if raw is not None:
            source = "cache"
    if raw is None and allow_gemini:
        raw = _gemini_candidates(block_lines, num_inputs, model, k, retries, feedback,
                                 thinking_level, timeout_s, fallback_models)
        if raw is not None:
            source = "gemini"
            if use_cache and not feedback:
                _save_cache(key, raw)

    candidates: list[Candidate] = []
    if raw is not None:
        for item in raw:
            try:
                prog = parse(item["instructions"])
                candidates.append(Candidate(prog, item.get("reason", ""), source))
            except (DSLError, KeyError, TypeError, AttributeError):
                continue  # ignore malformed entries
    else:
        # Gemini unavailable or failed: fall back to offline heuristics.
        candidates.extend(_manual_candidates(program, num_inputs))

    # Deduplicate by serialized program; drop the identity (same as input).
    seen = set()
    orig_sig = tuple(block_lines)
    unique: list[Candidate] = []
    for c in candidates:
        sig = tuple(serialize(c.program))
        if sig in seen or sig == orig_sig:
            continue
        seen.add(sig)
        unique.append(c)
    return unique


if __name__ == "__main__":
    from src.dsl import parse as _p
    prog = _p(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"])
    cands = propose(prog, 1, allow_gemini=bool(os.environ.get("GEMINI_API_KEY")))
    for c in cands:
        print(f"[{c.source}] {serialize(c.program)}  // {c.reason}")