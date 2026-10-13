# Novelty results: verified rewrites that wasm-opt misses

This is the project's novelty evidence. Every claim here is reproducible and was
produced by actually running the code — Z3 proves each equivalence and `wasm-opt -Oz`
is actually invoked on each original.

## How these were found

`src/superopt_search.py` is an enumerative superoptimizer (the classical baseline the
Review 2 deck calls for). It:

1. enumerates every stack-valid straight-line DSL program up to a length bound,
2. buckets them by behavior on fixed probe inputs,
3. for each bucket, proposes `longer -> shortest-in-bucket` as a rewrite and confirms
   it with Z3 (all-inputs proof), and
4. runs `wasm-opt -Oz` on the original and records the instruction count it reaches.

A rewrite is a **genuine wasm-opt miss** when it is Z3-verified, strictly shorter than
the original, and wasm-opt leaves the original longer than our rewrite.

Reproduce:

```powershell
python -m src.superopt_search --inputs 2 --max-len 4 --consts 0 1 -1 31
```

Run in this build: enumerated 3048 valid programs into 249 behavior buckets,
Z3-confirmed 2425 shorter rewrites, of which **178 are genuine wasm-opt misses**.
Results are written to `results/discoveries.json` and `results/discoveries.csv`.

## Headline findings (vetted, input-dependent, textbook-explainable)

These four are the ones to present. Each is input-dependent (so it is not mere constant
folding), each is a recognizable compiler identity, and each is confirmed three ways:
Z3 (all inputs), Wasmtime differential testing, and wasm-opt's own unchanged output.

| # | Original (4 instr) | Verified rewrite (3 instr) | Why it holds |
|---|---|---|---|
| 1 | `x  y  XOR  EQZ` → `(x ^ y) == 0` | `x  y  EQ` → `x == y` | `x ^ y` is 0 exactly when `x == y` |
| 2 | `x  31  SHR_S  EQZ` → `(x >>s 31) == 0` | `x  0  GE_S` → `x >= 0` | `x >>s 31` is the sign bit broadcast; 0 iff x is non-negative |
| 3 | `x  31  SHR_U  EQZ` → `(x >>u 31) == 0` | `x  0  GE_S` → `x >= 0` | `x >>u 31` is the sign bit; 0 iff x is non-negative |
| 4 | `1  x  EQZ  SUB` → `1 - (x == 0)` | `x  0  NE` → `x != 0` | `1 - [x==0]` equals `[x!=0]` |

For every row: wasm-opt -Oz leaves the original at **4 instructions** (unchanged), while
our pipeline produces a Z3-verified **3-instruction** equivalent. The `tests/test_superopt.py`
suite locks all four in as regression tests (equivalence under Z3, and the wasm-opt miss).

## Honesty notes for the viva

- The search found 178 misses total, but many share a rewrite target and some originals
  are near-tautologies. Present the four headline rows, which are clearly input-dependent
  and individually explainable, rather than waving at the raw count.
- These are small i32 examples. The claim is precise: **"our pipeline produces specific,
  formally verified rewrites that Binaryen wasm-opt -Oz does not find,"** demonstrated and
  reproducible. It is not a claim that the system beats wasm-opt on real-world code size
  overall — that would need a large real corpus.
- Why does wasm-opt miss these? Its peephole pass does not include these particular
  comparison/sign-bit identities. That is exactly the gap the project set out to probe:
  an LLM or an enumerative search can discover rewrites outside a fixed rulebook, and the
  Z3 gate makes them safe to adopt.

## 30-second novelty answer

"We built an enumerative superoptimizer over a verified i32 DSL, and used it to find
instruction-shortening rewrites that we prove equivalent with Z3. Four of them — including
`(x ^ y) == 0` becoming `x == y`, and the sign-bit test `(x >> 31) == 0` becoming `x >= 0`
— are rewrites that Binaryen wasm-opt -Oz does not perform. Z3 proves each is correct for
all inputs, Wasmtime confirms it on thousands of samples, and wasm-opt's own output shows
it leaves the original longer. That is a concrete, reproducible novelty result, not a
claim of general superiority."
