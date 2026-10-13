# Review 2 evidence map

Rubric: Implementation / prototype progress (5) · Novelty (2) · Documentation (1) ·
Presentation & Q&A (2). This maps the actual code to each requirement, names the file,
the command, the screenshot to capture, and a one-line viva answer.

> Honesty rule: the x+0 reduction is also found by `wasm-opt` — it proves the pipeline
> works, not novelty. The novelty evidence is separate: `docs/NOVELTY_RESULTS.md` lists
> Z3-verified rewrites (e.g. `(x^y)==0` → `x==y`) that `wasm-opt -Oz` does NOT find.
> Demo those live with `python -m src.pipeline examples\xor_eqz.wasm`.

## 1. System architecture
- **Shown by:** `docs/ARCHITECTURE.md` diagram + `src/pipeline.py` staged trace.
- **Capture:** the architecture diagram, and one full `--demo` run.
- **Viva:** "Three lanes — front-end, proposer, verifier — feeding a rule store. The LLM
  only proposes; acceptance needs both the differential-test gate and the Z3 proof."

## 2. Detailed design
- **Shown by:** `src/dsl.py` (opcode table, validator), `src/verifier.py` (bitvector encoding).
- **Capture:** the `OPSPECS` table and the `_binop` function in the editor.
- **Viva:** "The DSL is a 14-opcode stack language that maps one-to-one onto Z3 bitvectors
  and onto WAT."

## 3. Methodology / algorithm
- **Shown by:** `docs/METHODOLOGY.md`; the two gates in `difftest.py` and `verifier.py`.
- **Capture:** the methodology doc section on the two gates.
- **Viva:** "Differential testing is a cheap filter; Z3 is the proof. `unsat` = equivalent
  for all inputs; `unknown`/timeout is rejected."

## 4. Diagrams (choose what actually applies)
This project has **no database**, so do not invent one. Applicable diagrams:
- **System architecture diagram** — the pipeline (already in ARCHITECTURE.md).
- **Component diagram** — the three lanes and the rule-store feedback loop.
- **Data-flow diagram (DFD level 1)** — `.wasm` → block JSON → DSL → candidates → verdict → rule.
- **Sequence diagram** — pipeline → proposer → difftest → verifier → distiller for one block.
- **Flowchart** — the accept/reject decision (both gates → accept; either fails → reject).
- **Capture:** draw these from the ARCHITECTURE diagram; a UML class diagram of `dsl.Instr`,
  `verifier.VerifyResult`, and the rule dict is optional.

## 5. Module development
- **Shown by:** `src/` — all 10 modules implemented (M3–M10 plus DSL and experiment).
- **Capture:** the `src/` folder in VS Code's Explorer.
- **Viva:** "M1–M2 were done at Review 1; M3–M10 are now implemented and tested."

## 6. Working prototype
- **Shown by:** `python -m src.pipeline --demo`.
- **Capture:** the full `[1]…[10]` trace ending in ACCEPTED, 3→1, rule stored.
- **Viva:** "This is a live run, not a mock-up — the UNSAT comes from Z3 at runtime."

## 7. Implementation progress
- **Shown by:** this document + `pytest -v`.
- **Capture:** `pytest -v` showing 43 passing.
- **Viva:** "Verifier-first build order, exactly as the Review 2 checklist planned."

## 8. Test cases
- **Shown by:** `tests/` (see `docs/TESTING.md`).
- **Capture:** `pytest -v` output and the verifier case table.
- **Viva:** "Including the shift-mod-32 case that a naive encoding gets wrong."

## 9. Initial results
- **Shown by:** `python -m src.experiment` → `results/results.{json,csv}`;
  `python -m src.baseline` → `results/baseline.{json,csv}`.
- **Real numbers in this build:** 9 blocks in, 6 verified reductions, 12 instructions saved,
  `rewrites_wasmopt_misses = 0`. `wasm-opt` reduces x+0 to 1 instruction too.
- **Capture:** the experiment summary and `baseline.csv`.
- **Viva:** "On the identity blocks our reductions match wasm-opt. But the enumerative
  search (`python -m src.superopt_search`) found 178 Z3-verified shorter rewrites, of which
  four headline cases are genuine wasm-opt misses — see docs/NOVELTY_RESULTS.md and
  results/discoveries.csv."
- **Novelty capture:** `python -m src.superopt_search` output, `results/discoveries.csv`,
  and a live `python -m src.pipeline examples\xor_eqz.wasm` run (accepts a 4→3 rewrite
  wasm-opt leaves at 4).

## 10. Individual contribution
- **Shown by:** fill in per teammate (e.g. extractor+translator / verifier / proposer+cache /
  difftest+baseline+experiment). Use `git log --author=...` as evidence.
- **Capture:** `git shortlog -sne` once commits are split by author.
