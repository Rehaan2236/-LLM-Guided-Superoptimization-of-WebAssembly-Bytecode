# LLM-Guided Superoptimization of WebAssembly Bytecode

A **verify-gated superoptimizer** for WebAssembly. An LLM proposes shorter instruction
sequences for integer basic blocks, and a rewrite is accepted only if it survives two
independent correctness gates: differential testing in a real WASM runtime, and a
**Z3 SMT proof** of equivalence for all 2³² inputs. The LLM is never trusted on its own.

Verified rewrites are distilled into reusable peephole rules, so a repeated pattern is
optimized later with no LLM call and no re-proof.

## Highlights

- **Formally verified output.** Every accepted rewrite carries a Z3 `unsat` proof of
  equivalence under WebAssembly i32 semantics. Timeouts and `unknown` results are rejected.
- **Finds rewrites Binaryen misses.** An enumerative superoptimizer built on the same
  verifier found Z3-proven shorter sequences that `wasm-opt -Oz` leaves untouched, for
  example `(x ^ y) == 0 → x == y` and the sign-bit test `(x >> 31) == 0 → x >= 0`.
  See [Results](#results).
- **LLM with a feedback loop.** Gemini candidates that fail a gate are returned to the
  model together with the exact counterexample input that broke them.
- **Works offline.** Without an API key, a deterministic heuristic proposer keeps the
  whole pipeline runnable and testable.
- **Tested.** 54 pytest cases cover the DSL, translator, verifier (including shift-count
  masking), differential tester, proposer parsing, rule store and the end-to-end pipeline.
- **Local web UI.** A three-panel front end shows each pipeline stage with PASS/FAIL.

## How it works

```
.wasm
 │  extractor        wasm2wat → straight-line i32 basic blocks
 ▼
DSL program          24-opcode stack language (src/dsl.py)
 │  distiller        exact match against a verified rule? reuse it, done
 ▼
 │  proposer         Gemini proposes candidates (heuristic fallback offline)
 ▼
 │  validation       opcode whitelist, stack discipline, single i32 result
 ▼
 │  difftest         original vs candidate in Wasmtime, edge + random inputs
 ▼
 │  verifier         Z3 bitvector proof: unsat ⇒ equivalent for every input
 ▼
accepted rewrite ──► rule store (rules/rules.json)
```

Design choices worth knowing:

- **A constrained DSL, not raw WASM.** LLMs frequently emit structurally invalid
  bytecode. A small stack DSL is easy to generate, easy to validate, and maps one-to-one
  onto both Z3 bitvector operations and WAT instructions.
- **Two gates, different roles.** Differential testing is a cheap filter that rejects
  most bad candidates before the solver runs. Z3 is the sole authority for acceptance.
- **Conservative by construction.** Only strictly shorter candidates that pass both gates
  are accepted; otherwise the original block is kept.

More detail: [Architecture](docs/ARCHITECTURE.md) · [Methodology](docs/METHODOLOGY.md) ·
[Testing](docs/TESTING.md) · [Discovery results](docs/DISCOVERY_RESULTS.md)

## Results

`src/superopt_search.py` enumerates every stack-valid DSL program up to a length bound,
groups them by behavior, and proposes `longer → shortest-in-group` rewrites that Z3
then confirms. Each original is also run through `wasm-opt -Oz`.

On the default configuration (2 inputs, length ≤ 4, constants `0 1 -1 31`) it produced
**2,425 Z3-confirmed shorter rewrites**, of which **166 are not performed by `wasm-opt -Oz`**
(`results/discoveries.json`). Four representative, input-dependent cases:

| Original (4 instr) | Verified rewrite (3 instr) | Identity |
|---|---|---|
| `(x ^ y) == 0` | `x == y` | XOR is zero iff operands are equal |
| `(x >>s 31) == 0` | `x >= 0` (signed) | arithmetic shift extracts the sign |
| `(x >>u 31) == 0` | `x >= 0` (signed) | logical shift extracts the sign bit |
| `1 - (x == 0)` | `x != 0` | boolean negation |

`wasm-opt -Oz` leaves all four at 4 instructions. These are small i32 blocks, so the
claim is narrow: the system produces specific, formally verified rewrites outside
Binaryen's fixed rulebook. It is not a claim of beating `wasm-opt` on whole programs.

## Scope and limitations

- i32 only; straight-line blocks (no branches, loops, memory, floats or calls).
- Trap-free operations only; 1–3 parameters, ≤12 instructions, exactly one i32 result.
- Equivalence is proven within this modeled semantics.
- Rule reuse matches whole blocks exactly. Sub-sequence matching and operand
  generalization are the natural next steps, as is a larger real-world corpus.

## Quick start

Requires Python 3.12+, [WABT](https://github.com/WebAssembly/wabt/releases) (`wat2wasm`,
`wasm2wat`) and [Binaryen](https://github.com/WebAssembly/binaryen/releases) (`wasm-opt`)
on `PATH`. Commands below are PowerShell; the code itself is cross-platform.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\scripts\verify_environment.ps1      # checks all tools
.\scripts\compile_examples.ps1        # builds examples/*.wasm from .wat
```

Optional: put `GEMINI_API_KEY=...` in a git-ignored `.env` to enable the LLM proposer.
The model and limits are configured in `config.json`. Without a key the pipeline uses
the offline heuristic proposer.

## Usage

```powershell
# Optimize one module end to end (staged trace, ending in ACCEPTED / rule stored)
python -m src.pipeline examples\xor_eqz.wasm
python -m src.pipeline --demo                  # x + 0 → x, then a rule-hit rerun
python -m src.pipeline examples\x_plus_zero.wasm --no-gemini

# Search for verified rewrites that wasm-opt misses
python -m src.superopt_search --inputs 2 --max-len 4 --consts 0 1 -1 31

# Compare against wasm-opt; run the example corpus
python -m src.baseline examples\x_plus_zero.wasm examples\x_times_two.wasm
python -m src.experiment

# Individual components
python -m src.verifier        # Z3 equivalence cases
python -m src.difftest        # differential testing demo
python -m src.proposer        # candidate generation

# Tests
pytest -v

# Web UI at http://127.0.0.1:8000
.\scripts\run_ui.ps1
```

## Project layout

```
src/        dsl · wasm_translator · extractor · proposer · difftest · verifier
            distiller · baseline · pipeline · experiment · superopt_search
app/        local web UI (server.py, static/index.html)
tests/      pytest suite (unit + integration; integration tests skip without WABT/Wasmtime)
examples/   identity and strength-reduction blocks as .wat/.wasm
rules/      verified peephole rules
results/    experiment, baseline and discovery outputs (json/csv)
docs/       architecture, methodology, testing, discovery results
scripts/    setup, environment check, example build, demo and UI launchers
```

## Tech stack

Python · Z3 (SMT) · Wasmtime · WABT · Binaryen · Google Gemini API · pytest
