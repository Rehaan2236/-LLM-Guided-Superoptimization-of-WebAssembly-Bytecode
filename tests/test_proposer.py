"""
Tests for the proposer. These run entirely offline (allow_gemini=False) so they
never need the API. They exercise JSON extraction, dedup, and the manual fallback.
"""
from src.dsl import parse, serialize
from src.proposer import propose, _extract_json, _manual_candidates


def test_manual_fallback_x_plus_zero():
    prog = parse(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"])
    cands = propose(prog, 1, allow_gemini=False, use_cache=False)
    sigs = [serialize(c.program) for c in cands]
    assert ["PUSH_INPUT 0"] in sigs


def test_manual_fallback_x_times_zero():
    prog = parse(["PUSH_INPUT 0", "PUSH_CONST 0", "MUL"])
    cands = _manual_candidates(prog, 1)
    sigs = [serialize(c.program) for c in cands]
    assert ["PUSH_CONST 0"] in sigs


def test_candidates_exclude_identity():
    prog = parse(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"])
    cands = propose(prog, 1, allow_gemini=False, use_cache=False)
    # the original program must never be returned as its own candidate
    assert all(serialize(c.program) != serialize(prog) for c in cands)


def test_candidates_deduplicated():
    prog = parse(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"])
    cands = propose(prog, 1, allow_gemini=False, use_cache=False)
    sigs = [tuple(serialize(c.program)) for c in cands]
    assert len(sigs) == len(set(sigs))


def test_extract_json_from_fenced_block():
    text = '```json\n{"candidates": [{"instructions": ["PUSH_INPUT 0"]}]}\n```'
    data = _extract_json(text)
    assert data["candidates"][0]["instructions"] == ["PUSH_INPUT 0"]


def test_extract_json_from_noisy_text():
    text = 'Sure! Here you go:\n{"candidates": []}\nHope that helps.'
    assert _extract_json(text) == {"candidates": []}
