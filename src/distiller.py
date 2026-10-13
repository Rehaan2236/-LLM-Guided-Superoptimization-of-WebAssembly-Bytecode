"""
distiller.py — M9: peephole rule distillation and reuse.

When a rewrite is verified (passes differential testing AND Z3), we store it as a
reusable peephole rule: an exact pattern of DSL instructions and its verified
replacement. Before asking Gemini about a new block, the pipeline first checks the
rule store; a hit lets us reuse a proven rewrite without calling the LLM or
re-running verification.

V1 uses EXACT whole-block pattern matching (the stored pattern must equal the
block's instruction list, operands included). That is intentionally simple and
safe. Sub-sequence matching and operand generalization are V2 work.

Rules live in rules/rules.json as a list of objects:
  {
    "pattern":     ["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"],
    "replacement": ["PUSH_INPUT 0"],
    "num_inputs":  1,
    "verified":    true,
    "reduction":   2,
    "times_seen":  1
  }
"""

from __future__ import annotations

import json
from pathlib import Path

from src.dsl import Instr, parse, serialize

DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "rules" / "rules.json"


def _rules_path(path: Path | None) -> Path:
    """Resolve the rules file at CALL time (not import time), so tests can
    redirect DEFAULT_RULES_PATH to a temporary file."""
    return path if path is not None else DEFAULT_RULES_PATH


def load_rules(path: Path | None = None) -> list[dict]:
    path = _rules_path(path)
    if not path.exists():
        return []
    text = path.read_text().strip()
    if not text:
        return []
    return json.loads(text)


def save_rules(rules: list[dict], path: Path | None = None) -> None:
    path = _rules_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rules, indent=2) + "\n")


def match_rule(program: list[Instr], rules: list[dict]) -> dict | None:
    """Return the first rule whose pattern exactly equals `program`, else None."""
    prog_lines = serialize(program)
    for rule in rules:
        if rule.get("verified") and rule["pattern"] == prog_lines:
            return rule
    return None


def record_rule(
    original: list[Instr],
    replacement: list[Instr],
    num_inputs: int,
    *,
    path: Path | None = None,
) -> dict:
    """
    Add (or bump) a verified rule for original -> replacement. Returns the rule.
    Only call this for rewrites that have ALREADY passed Z3.
    """
    rules = load_rules(path)
    pattern = serialize(original)
    replacement_lines = serialize(replacement)

    for rule in rules:
        if rule["pattern"] == pattern:
            rule["times_seen"] = rule.get("times_seen", 1) + 1
            rule["replacement"] = replacement_lines
            rule["reduction"] = len(original) - len(replacement)
            save_rules(rules, path)
            return rule

    rule = {
        "pattern": pattern,
        "replacement": replacement_lines,
        "num_inputs": num_inputs,
        "verified": True,
        "reduction": len(original) - len(replacement),
        "times_seen": 1,
    }
    rules.append(rule)
    save_rules(rules, path)
    return rule


def rule_replacement_program(rule: dict) -> list[Instr]:
    """Parse a rule's replacement back into a DSL program."""
    return parse(rule["replacement"])
