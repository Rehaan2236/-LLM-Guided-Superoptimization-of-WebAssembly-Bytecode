# Architecture

## System overview

The system is a verify-gated superoptimizer for WebAssembly integer basic blocks.
An LLM proposes cheaper instruction sequences; two independent gates decide whether
a proposal is accepted. The LLM never decides anything on its own.

```
.wasm
  │  extractor.py            parse wasm2wat output into straight-line i32 blocks
  ▼
basic block (JSON)
  │  wasm_translator.py      WASM instructions -> constrained DSL
  ▼
DSL program
  │  distiller.py            exact-match an existing verified rule? reuse it, done
  ▼
  │  proposer.py            Gemini proposes candidates (manual fallback offline)
  ▼
candidate DSL programs
  │  (DSL) dsl.validate          whitelist + stack + single-result checks
  ▼
  │  difftest.py            run both in Wasmtime on edge + random inputs (filter)
  ▼
  │  verifier.py            Z3 proves equivalence for ALL inputs (proof)
  ▼
accept shortest verified rewrite
  │  compare instruction counts
  ▼
  │  distiller.py           store verified rewrite as a reusable peephole rule
  ▼
rules/rules.json
```

## Module responsibilities

| Module | File | Role |
|---|---|---|
| DSL foundation | `src/dsl.py` | opcode table, parser, serializer, stack validator, 32-bit reference interpreter |
| Translator | `src/wasm_translator.py` | WASM instr ↔ DSL; DSL → WAT module text |
| Extractor | `src/extractor.py` | `.wasm` → `wasm2wat` → block JSON; skips unsupported functions with a reason |
| Verifier | `src/verifier.py` | Z3 bitvector symbolic execution + equivalence query |
| Diff tester | `src/difftest.py` | Wasmtime execution of original vs candidate on many inputs |
| Proposer | `src/proposer.py` | Gemini prompt/parse/retry/cache + manual heuristic fallback |
| Distiller | `src/distiller.py` | verified-rule store: load, exact-match, record |
| Baseline | `src/baseline.py` | `wasm-opt -Oz` instruction/byte comparison → CSV/JSON |
| Pipeline | `src/pipeline.py` | wires all modules, runs the counterexample feedback loop, prints the staged trace, `--demo` |
| Experiment | `src/experiment.py` | runs the example corpus, writes `results/*.{json,csv}` |
| Search | `src/superopt_search.py` | enumerative superoptimizer; Z3-confirms shorter rewrites and checks them against `wasm-opt -Oz` |

## Data flow and key representations

- **Block JSON** (extractor output): `{function, params, result, skipped, instructions:[{op,...}]}`.
- **DSL program**: a `list[Instr]`, each `Instr(op, operand?)`. Serialized form is one
  instruction per line, e.g. `PUSH_INPUT 0`, `PUSH_CONST 0`, `ADD`.
- **Rule**: `{pattern, replacement, num_inputs, verified, reduction, times_seen}`.

## Counterexample feedback loop

If every candidate from the LLM fails a gate, the failures and the exact inputs that broke
them are sent back to the model for another round (`llm_feedback_rounds` in `config.json`).

## The two correctness gates

1. **Differential testing** executes real WASM in Wasmtime over fixed edge
   values (0, 1, −1, INT32_MIN, INT32_MAX, bit patterns) plus random vectors. It is a
   fast filter: agreement is *evidence*, never proof.
2. **Z3 verification** encodes both programs as 32-bit bitvector formulas and asks
   whether any input makes them differ. `unsat` means equivalent for all inputs under
   the modeled semantics; `sat` yields a counterexample; `unknown`/timeout is rejected.

A rewrite is accepted only if it passes both gates. Z3 is the authority; differential
testing exists to reject obviously-wrong candidates cheaply before the solver runs.

## Modeled semantics (what "equivalent" means)

All values are 32-bit. The DSL has 24 opcodes, including rotates and signed/unsigned comparisons. `ADD`/`SUB`/`MUL` wrap mod 2³². `SHL`/`SHR_*` take the shift
count mod 32 (WASM semantics), so `x << 40` equals `x << 8`. Signedness is a property
of the operation (`SHR_S` arithmetic vs `SHR_U` logical), not of the value. Every
opcode is total (never traps), so equivalence is simply equality of the i32 result for
every input. Equivalence claims hold only within this modeled semantics.
