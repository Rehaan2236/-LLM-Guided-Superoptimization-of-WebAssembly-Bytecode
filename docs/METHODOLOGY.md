# Methodology

## 1. Basic-block extraction

A `.wasm` file is disassembled with WABT's `wasm2wat`, producing one instruction per
line. A line-oriented parser reads each function header for its i32 parameter count and
result type, then reads the body. Because the supported subset has no control flow, each function body is a
single straight-line basic block. Any function that uses a non-i32 type or an instruction
outside the supported whitelist is reported as `skipped` with a reason, never processed silently.

## 2. The constrained DSL

Rather than have the LLM emit raw WebAssembly (which it frequently gets syntactically or
structurally wrong), the system uses a tiny stack-based DSL. It has 24 opcodes:

```
PUSH_INPUT k   PUSH_CONST c
ADD SUB MUL AND OR XOR SHL SHR_S SHR_U
EQZ EQ NE
ROTL ROTR LT_S LT_U GT_S GT_U LE_S LE_U GE_S GE_U
```

The DSL is easy to generate, easy to validate (whitelist + stack discipline + single
result), and maps one-to-one onto both Z3 bitvector operations and WAT instructions.
`wasm_translator.py` converts WASM instructions to DSL and DSL to a complete WAT module
(so the differential tester can compile and run it).

## 3. Candidate generation

The proposer sends the DSL block to Gemini with a strict-JSON prompt asking for shorter
equivalent rewrites. The response is parsed defensively (fenced-code and noisy-text
tolerant), malformed replies trigger a retry, candidates that fail a gate are fed back with their counterexamples, duplicates are dropped, and results are
cached on disk keyed by the block. If no API key is set or the call fails, deterministic
offline heuristics supply candidates so the correctness pipeline still runs. The proposer
only *proposes*; it never accepts.

## 4. Differential testing

Original and candidate are each wrapped in a WAT module, compiled with `wat2wasm`, and
executed in Wasmtime. They are run on the same inputs: the cross-product of edge values
(0, 1, −1, 2, INT32_MIN, INT32_MAX, and bit patterns) plus random i32 vectors up to the
configured count. The first disagreement is recorded as a counterexample and the candidate
is rejected. Passing is evidence of equivalence, not proof.

## 5. SMT verification

Both programs are symbolically executed over Z3 `BitVec(32)` variables (one per input),
yielding an output bitvector each. The solver is asked whether `original_out != candidate_out`
is satisfiable:

- `unsat` → no distinguishing input exists → **equivalent** → accept.
- `sat` → a counterexample exists → **not equivalent** → reject (the model is reported).
- `unknown`/timeout → **unverified** → reject conservatively.

Bitvector width and operations mirror WASM i32 semantics, including the shift-count-mod-32
rule, which is the main correctness subtlety.

## 6. Acceptance logic

A candidate is accepted only if it is strictly shorter than the original **and** passes both
gates. Candidates are tried shortest-first; the first that verifies is accepted.

## 7. Rule distillation

Each accepted rewrite is stored in `rules/rules.json` as an exact pattern → replacement rule
with its instruction reduction and a `times_seen` counter. Before invoking Gemini on a new
block, the pipeline checks the rule store; an exact match reuses the proven rewrite with no
LLM call and no re-verification. Matching is exact and whole-block; sub-sequence matching and
operand generalization are future work.
