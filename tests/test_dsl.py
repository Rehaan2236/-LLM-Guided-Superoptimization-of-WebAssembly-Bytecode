"""Unit tests for the DSL: parser, serializer, validator, interpreter."""
import pytest

from src.dsl import (
    parse, serialize, validate, interpret, to_s32, to_u32,
    DSLError, instruction_count,
)


def test_parse_and_serialize_roundtrip():
    lines = ["PUSH_INPUT 0", "PUSH_CONST 5", "ADD"]
    prog = parse(lines)
    assert serialize(prog) == lines
    assert instruction_count(prog) == 3


def test_parse_ignores_comments_and_blanks():
    prog = parse(["# comment", "", "PUSH_INPUT 0"])
    assert len(prog) == 1


def test_parse_unknown_opcode():
    with pytest.raises(DSLError):
        parse(["FROB 1"])


def test_parse_operand_required():
    with pytest.raises(DSLError):
        parse(["PUSH_CONST"])          # missing operand
    with pytest.raises(DSLError):
        parse(["ADD 3"])               # unexpected operand


def test_validate_single_result_ok():
    validate(parse(["PUSH_INPUT 0", "PUSH_CONST 0", "ADD"]), 1)


def test_validate_rejects_bad_input_index():
    with pytest.raises(DSLError):
        validate(parse(["PUSH_INPUT 3"]), 1)   # only input 0 exists


def test_validate_rejects_underflow():
    with pytest.raises(DSLError):
        validate(parse(["ADD"]), 1)            # nothing to add


def test_validate_rejects_multiple_results():
    with pytest.raises(DSLError):
        validate(parse(["PUSH_INPUT 0", "PUSH_INPUT 0"]), 1)  # two left


def test_validate_rejects_too_long():
    prog = parse(["PUSH_INPUT 0"] + ["PUSH_CONST 0", "ADD"] * 10)
    with pytest.raises(DSLError):
        validate(prog, 1, max_len=12)


def test_interpret_wraps_mod_2_32():
    # INT32_MAX + 1 wraps to INT32_MIN
    prog = parse(["PUSH_INPUT 0", "PUSH_CONST 1", "ADD"])
    assert to_s32(interpret(prog, [2**31 - 1])) == -(2**31)


def test_interpret_shift_count_mod_32():
    prog = parse(["PUSH_INPUT 0", "PUSH_CONST 40", "SHL"])
    assert interpret(prog, [1]) == (1 << 8)   # 40 & 31 == 8


def test_interpret_signed_vs_unsigned_shift():
    neg = -16  # 0xFFFFFFF0
    s = parse(["PUSH_INPUT 0", "PUSH_CONST 1", "SHR_S"])
    u = parse(["PUSH_INPUT 0", "PUSH_CONST 1", "SHR_U"])
    assert to_s32(interpret(s, [neg])) == -8          # sign-extended
    assert interpret(u, [neg]) == (to_u32(neg) >> 1)  # zero-filled


def test_interpret_eqz_eq_ne():
    assert interpret(parse(["PUSH_INPUT 0", "EQZ"]), [0]) == 1
    assert interpret(parse(["PUSH_INPUT 0", "EQZ"]), [5]) == 0
