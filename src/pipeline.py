"""
pipeline.py — M10: the complete end-to-end pipeline.

Flow:
  .wasm
    -> extract basic block            (M3)
    -> translate to DSL               (M4)
    -> check existing peephole rules  (M9)   [reuse without LLM if hit]
    -> propose candidates             (M5)   [Gemini, or manual fallback]
    -> validate each candidate        (M4/DSL)
    -> differential test              (M6)   [fast filter]
    -> Z3 equivalence verification    (M7)   [proof]
    -> accept the shortest verified rewrite
    -> compare instruction counts
    -> distil a verified peephole rule (M9)

Nothing is faked: every verdict printed comes from the actual modules. If the
candidate pool is empty or nothing verifies, the pipeline says so and keeps the
original program.

Usage:
  python -m src.pipeline examples/x_plus_zero.wasm
  python -m src.pipeline --demo                 (runs the x+0 story end to end)
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.dsl import Instr, validate, serialize, instruction_count, DSLError
from src.extractor import first_supported_block
from src.wasm_translator import wasm_instrs_to_dsl
from src.proposer import propose, Candidate
from src.difftest import differential_test
from src.verifier import verify
from src.distiller import load_rules, match_rule, record_rule, rule_replacement_program

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"


def load_config() -> dict:
    defaults = {
        "max_original_instructions": 12,
        "max_candidate_instructions": 12,
        "max_stack_depth": 8,
        "differential_tests": 1000,
        "z3_timeout_ms": 5000,
        "gemini_model": "gemini-3.5-flash",
        "cache_enabled": True,
        "llm_feedback_rounds": 3,
        "gemini_thinking": "low",
        "gemini_timeout_s": 90,
        "gemini_fallback_models": ['gemini-3.7-flash', 'gemini-3.8-flash', 'gemini-flash-latest', 'gemini-3.1-flash-lite'],
    }
    if CONFIG_PATH.exists():
        defaults.update(json.loads(CONFIG_PATH.read_text()))
    return defaults


def _p(label: str, value: str = "") -> None:
    print(f"{label} {value}".rstrip())


def optimize_block(
    program: list[Instr],
    num_inputs: int,
    cfg: dict,
    *,
    allow_gemini: bool = True,
    verbose: bool = True,
) -> dict:
    """
    Run the full optimize loop on one DSL block. Returns a result dict and
    (as a side effect) prints the staged trace when verbose.
    """
    model = cfg["gemini_model"]
    result = {
        "original": serialize(program),
        "num_inputs": num_inputs,
        "accepted": False,
        "rewrite": None,
        "original_instr": instruction_count(program),
        "rewrite_instr": None,
        "source": None,
        "z3": None,
        "rule_reused": False,
    }

    # Validate the input block itself.
    validate(program, num_inputs,
             max_len=cfg["max_original_instructions"],
             max_stack=cfg["max_stack_depth"])

    # (M9) rule lookup first.
    if verbose:
        _p("[3] Checking existing rules...")
    rules = load_rules()
    hit = match_rule(program, rules)
    if hit:
        repl = rule_replacement_program(hit)
        if verbose:
            _p("    [RULE HIT]", f"reusing verified rule (seen {hit['times_seen']}x)")
            _p("[4] LLM skipped (rule reuse)")
        result.update(accepted=True, rewrite=serialize(repl),
                      rewrite_instr=instruction_count(repl),
                      source="rule", rule_reused=True, z3="cached")
        record_rule(program, repl, num_inputs)  # bump times_seen
        if verbose:
            _print_counts(result)
        return result
    if verbose:
        _p("    [NO MATCH]")

    # (M5) propose candidates, with a counterexample feedback loop:
    # if every Gemini candidate fails a gate, the failures (with the exact
    # inputs that broke them) are sent back to Gemini and it is asked again.
    from src.dsl import interpret, to_s32
    max_rounds = int(cfg.get("llm_feedback_rounds", 3))
    feedback: list[dict] = []
    tried: set[tuple] = set()
    best = None
    idx = 0
    for rnd in range(1, max_rounds + 1):
        if verbose:
            if rnd == 1:
                _p("[4] Generating candidates...")
            else:
                _p(f"\n[4] Asking Gemini again with {len(feedback)} counterexample(s) "
                   f"(round {rnd} of {max_rounds})...")
        cands: list[Candidate] = propose(
            program, num_inputs, model=model,
            use_cache=cfg["cache_enabled"], allow_gemini=allow_gemini,
            feedback=feedback or None,
            thinking_level=cfg.get("gemini_thinking", "low"),
            timeout_s=int(cfg.get("gemini_timeout_s", 90)),
            fallback_models=cfg.get("gemini_fallback_models", []),
        )
        cands = [c for c in cands if tuple(serialize(c.program)) not in tried]
        srcs = {c.source for c in cands}
        if verbose:
            if not cands:
                _p("    [NONE] no new candidates proposed")
            else:
                if srcs == {"manual"}:
                    _p("    [LLM unavailable — using manual demonstration candidate]")
                _p("   ", f"{len(cands)} candidate(s) from {sorted(srcs)}")
        if not cands:
            break

        # Try candidates shortest-first; accept the first that verifies.
        cands.sort(key=lambda c: instruction_count(c.program))
        for cand in cands:
            tried.add(tuple(serialize(cand.program)))
            if instruction_count(cand.program) >= instruction_count(program):
                continue  # not a reduction
            idx += 1
            if verbose:
                _p(f"\n    Candidate {idx} [{cand.source}]:",
                   f"{serialize(cand.program)}  // {cand.reason}")

            # (M4/DSL) validate
            if verbose:
                _p("[5] Validating candidate...")
            try:
                validate(cand.program, num_inputs,
                         max_len=cfg["max_candidate_instructions"],
                         max_stack=cfg["max_stack_depth"])
            except DSLError as e:
                if verbose:
                    _p("    [INVALID]", str(e))
                continue
            if verbose:
                _p("    [PASS]")

            # (M6) differential testing
            if verbose:
                _p("[6] Differential testing...")
            diff = differential_test(program, cand.program, num_inputs,
                                     num_tests=cfg["differential_tests"])
            if not diff.passed:
                if verbose:
                    _p("    [FAIL]", f"counterexample {diff.counterexample}")
                ce = diff.counterexample
                feedback.append({"instructions": serialize(cand.program),
                                 "inputs": ce["inputs"], "original": ce["original"],
                                 "candidate": ce["candidate"]})
                continue
            if verbose:
                _p("    [PASS]", f"{diff.tests} inputs agreed (evidence, not proof)")

            # (M7) Z3 verification
            if verbose:
                _p("[7] Z3 verification...")
            vr = verify(program, cand.program, num_inputs,
                        timeout_ms=cfg["z3_timeout_ms"])
            if verbose:
                _p("   ", f"{vr.z3_result.upper()} -> {vr.status}")
            if vr.accepted:
                best = (cand, vr)
                break
            if vr.counterexample:
                if verbose:
                    _p("    [REJECT]", f"counterexample {vr.counterexample}")
                xs = [vr.counterexample[f"in{i}"] for i in range(num_inputs)]
                feedback.append({"instructions": serialize(cand.program),
                                 "inputs": vr.counterexample,
                                 "original": to_s32(interpret(program, xs)),
                                 "candidate": to_s32(interpret(cand.program, xs))})

        if best is not None:
            break
        # Only Gemini can learn from feedback; the offline list can't.
        if srcs <= {"manual"} or not feedback:
            break

    if best is None:
        if verbose:
            _p("\n[8] No candidate verified. Keeping original.")
        return result

    cand, vr = best
    result.update(
        accepted=True, rewrite=serialize(cand.program),
        rewrite_instr=instruction_count(cand.program),
        source=cand.source, z3=vr.z3_result,
    )
    if verbose:
        _p("\n[8] Candidate ACCEPTED", "(both gates cleared)")
    _print_counts(result) if verbose else None

    # (M9) distil
    if verbose:
        _p("[10] Distilling verified rule...")
    rule = record_rule(program, cand.program, num_inputs)
    if verbose:
        _p("    [OK]", f"rule stored (reduction {rule['reduction']} instr)")
    return result


def _print_counts(result: dict) -> None:
    _p("\n[9] Instruction count:")
    _p("    Original:", str(result["original_instr"]))
    _p("    Rewrite: ", str(result["rewrite_instr"]))


def run_on_wasm(wasm_path: str | Path, *, allow_gemini: bool = True) -> dict:
    cfg = load_config()
    print("=" * 44)
    print("WASM SUPEROPTIMIZER")
    print("=" * 44)
    _p("\n[1] Extracting basic block...", str(wasm_path))
    block = first_supported_block(wasm_path)
    num_inputs = block["params"]
    _p("    [OK]", f"{len(block['instructions'])} instr, {num_inputs} input(s)")

    _p("\n[2] Translating to DSL...")
    program = wasm_instrs_to_dsl(block["instructions"])
    _p("    [OK]", str(serialize(program)))
    print()

    result = optimize_block(program, num_inputs, cfg, allow_gemini=allow_gemini)
    print("\n" + "=" * 44)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM-guided WASM superoptimizer")
    ap.add_argument("wasm", nargs="?", help="path to a .wasm file")
    ap.add_argument("--demo", action="store_true",
                    help="run the x+0 demonstration (manual fallback if no API key)")
    ap.add_argument("--no-gemini", action="store_true",
                    help="force the manual fallback, never call Gemini")
    args = ap.parse_args()

    allow_gemini = not args.no_gemini
    if args.demo:
        demo_path = ROOT / "examples" / "x_plus_zero.wasm"
        if not demo_path.exists():
            print("Demo file missing. Run: wat2wasm examples\\x_plus_zero.wat "
                  "-o examples\\x_plus_zero.wasm")
            return
        # The demo intentionally does not require the API.
        if not os.environ.get("GEMINI_API_KEY"):
            allow_gemini = False
        run_on_wasm(demo_path, allow_gemini=allow_gemini)
        return

    if not args.wasm:
        ap.print_help()
        return
    run_on_wasm(args.wasm, allow_gemini=allow_gemini)


if __name__ == "__main__":
    main()