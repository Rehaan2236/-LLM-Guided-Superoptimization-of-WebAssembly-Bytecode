"""Tests for the peephole rule store (isolated to a temp rules file)."""
from pathlib import Path

from src.dsl import parse, serialize
from src.distiller import (
    load_rules, save_rules, match_rule, record_rule, rule_replacement_program,
)


def test_record_and_match(tmp_path: Path):
    rules_file = tmp_path / "rules.json"
    save_rules([], rules_file)
    orig = parse(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"])
    repl = parse(["PUSH_INPUT 0"])

    rule = record_rule(orig, repl, 1, path=rules_file)
    assert rule["verified"] and rule["reduction"] == 2

    rules = load_rules(rules_file)
    hit = match_rule(orig, rules)
    assert hit is not None
    assert serialize(rule_replacement_program(hit)) == ["PUSH_INPUT 0"]


def test_no_match_for_unseen(tmp_path: Path):
    rules_file = tmp_path / "rules.json"
    save_rules([], rules_file)
    record_rule(parse(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"]),
                parse(["PUSH_INPUT 0"]), 1, path=rules_file)
    other = parse(["PUSH_INPUT 0", "PUSH_CONST 1", "ADD"])
    assert match_rule(other, load_rules(rules_file)) is None


def test_times_seen_increments(tmp_path: Path):
    rules_file = tmp_path / "rules.json"
    save_rules([], rules_file)
    orig = parse(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"])
    repl = parse(["PUSH_INPUT 0"])
    record_rule(orig, repl, 1, path=rules_file)
    r2 = record_rule(orig, repl, 1, path=rules_file)
    assert r2["times_seen"] == 2
