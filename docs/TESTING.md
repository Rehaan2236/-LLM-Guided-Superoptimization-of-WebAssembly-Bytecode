# Testing

Run everything from the project root with the venv active:

```powershell
pytest -v
```

43 tests pass in this environment. Tests split into **unit tests** (no external tools)
and **integration tests** (need WABT / Wasmtime). Integration tests auto-skip when the
tools are missing, so the unit suite always runs.

## Unit tests (no external tools)

| File | Covers |
|---|---|
| `test_dsl.py` | parser, serializer, stack validator (underflow, bad index, multiple results, too-long), 32-bit wrap, shift-mod-32, signed vs unsigned shift, EQZ |
| `test_translator.py` | WASM→DSL, unsupported-instruction rejection, DSL→WAT module |
| `test_verifier.py` | all equivalence cases below — pure Z3, no WASM runtime |
| `test_distiller.py` | record, exact match, no-match, `times_seen` increment (isolated temp file) |
| `test_proposer.py` | manual fallback, identity exclusion, dedup, JSON extraction from fenced/noisy text |
| `test_difftest.py` (reference path) | agreement and mismatch using the pure-Python interpreter |

## Integration tests (need WABT / Wasmtime)

| File | Covers |
|---|---|
| `test_extractor.py` | extract real compiled `x_plus_zero.wasm` and `x_times_two.wasm` |
| `test_difftest.py` (Wasmtime path) | run original vs candidate as real WASM, agreement and mismatch |
| `test_pipeline.py` | full `optimize_block` on x+0: accepted, rewrite `PUSH_INPUT 0`, 3→1, z3 unsat |

## Correctness cases in the verifier

| Case | Expected |
|---|---|
| x + 0 vs x | EQUIVALENT (unsat) |
| x + 1 vs x | NOT_EQUIVALENT (sat, counterexample) |
| x * 2 vs x + x | EQUIVALENT |
| x + 1 vs x + 2 | NOT_EQUIVALENT |
| x * 0 vs 0 | EQUIVALENT |
| x * 1 vs x | EQUIVALENT |
| x − 0 vs x | EQUIVALENT |
| x \| 0 vs x | EQUIVALENT |
| x ^ 0 vs x | EQUIVALENT |
| x << 40 vs x << 8 | EQUIVALENT (shift count mod 32) |
| x << 40 vs 0 | NOT_EQUIVALENT (guards against a broken shift model) |

## Edge cases exercised

- Stack underflow, out-of-range input index, non-single result, over-length program.
- 32-bit overflow wraparound (INT32_MAX + 1 = INT32_MIN).
- Shift count ≥ 32 (masked to 5 bits).
- Malformed / fenced / noisy LLM JSON.
- Z3 `unknown`/timeout path (rejected conservatively — see `verifier.VerifyResult`).
