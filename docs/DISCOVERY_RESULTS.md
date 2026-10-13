# Discovery results: verified rewrites that wasm-opt misses

Every result here is reproducible. Z3 proves each equivalence and `wasm-opt -Oz` is
actually invoked on each original.

## Method

`src/superopt_search.py` is an enumerative superoptimizer. It:

1. enumerates every stack-valid straight-line DSL program up to a length bound,
2. groups programs by behavior on fixed probe inputs,
3. for each group, proposes `longer -> shortest-in-group` as a rewrite and confirms it
   with Z3 (a proof over all inputs), and
4. runs `wasm-opt -Oz` on the original and records the instruction count it reaches.

A rewrite counts as a **wasm-opt miss** when it is Z3-verified, strictly shorter than
the original, and `wasm-opt -Oz` leaves the original longer than the rewrite.

```powershell
python -m src.superopt_search --inputs 2 --max-len 4 --consts 0 1 -1 31
```

This run produced 2,425 Z3-confirmed shorter rewrites, of which 166 are wasm-opt misses
(`results/discoveries.json`, `results/discoveries.csv`).

## Representative findings

Each is input-dependent (not constant folding), corresponds to a recognizable compiler
identity, and is confirmed three ways: Z3 (all inputs), Wasmtime differential testing,
and wasm-opt's own unchanged output.

| # | Original (4 instr) | Verified rewrite (3 instr) | Why it holds |
|---|---|---|---|
| 1 | `x  y  XOR  EQZ` → `(x ^ y) == 0` | `x  y  EQ` → `x == y` | `x ^ y` is 0 exactly when `x == y` |
| 2 | `x  31  SHR_S  EQZ` → `(x >>s 31) == 0` | `x  0  GE_S` → `x >= 0` | `x >>s 31` broadcasts the sign bit; 0 iff x is non-negative |
| 3 | `x  31  SHR_U  EQZ` → `(x >>u 31) == 0` | `x  0  GE_S` → `x >= 0` | `x >>u 31` is the sign bit; 0 iff x is non-negative |
| 4 | `1  x  EQZ  SUB` → `1 - (x == 0)` | `x  0  NE` → `x != 0` | `1 - [x==0]` equals `[x!=0]` |

In every row `wasm-opt -Oz` leaves the original at 4 instructions, while the pipeline
produces a Z3-verified 3-instruction equivalent. `tests/test_superopt.py` locks all four
in as regression tests (Z3 equivalence and the wasm-opt miss).

## Interpretation and limits

- Many of the 166 misses share a rewrite target and some originals are near-tautologies,
  so the four rows above are the meaningful ones rather than the raw count.
- These are small i32 blocks. The precise claim is that the system produces specific,
  formally verified rewrites that Binaryen `wasm-opt -Oz` does not find. It does not
  claim to beat `wasm-opt` on real-world code size; that needs a large real corpus.
- Binaryen's peephole pass does not include these particular comparison and sign-bit
  identities. Enumerative or LLM-driven search can discover rewrites outside a fixed
  rulebook, and the Z3 gate makes them safe to adopt.
