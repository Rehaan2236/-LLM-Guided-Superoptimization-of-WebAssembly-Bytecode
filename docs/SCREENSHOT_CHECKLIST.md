# Screenshot checklist

Capture these in order. Every command works in this build; none asks you to screenshot a
feature that does not run. Run all commands from the project root with the venv active.
Save files to `docs/screenshots/` with the suggested names.

| # | Screenshot | Command / view | Save as |
|---|---|---|---|
| 1 | VS Code project folder | VS Code Explorer showing `src/`, `tests/`, `examples/`, `docs/` | `01_project.png` |
| 2 | Environment verification | `.\scripts\verify_environment.ps1` | `02_env.png` |
| 3 | Example WAT file | open `examples\x_plus_zero.wat` | `03_wat.png` |
| 4 | WASM extraction (M3) | `python -m src.extractor examples\x_plus_zero.wasm` | `04_extract.png` |
| 5 | DSL representation (M4) | `python -c "from src.extractor import first_supported_block as f; from src.wasm_translator import wasm_instrs_to_dsl as g; from src.dsl import serialize; print(serialize(g(f('examples/x_plus_zero.wasm')['instructions'])))"` | `05_dsl.png` |
| 6 | Z3 UNSAT proof (M7) | `python -m src.verifier` (first line: x+0 EQUIVALENT unsat) | `06_z3_unsat.png` |
| 7 | Z3 SAT counterexample (M7) | same output, the `x+1 vs x` line with `ce=` | `07_z3_sat.png` |
| 8 | Differential testing (M6) | `python -m src.difftest` | `08_difftest.png` |
| 9 | Gemini candidate (M5) | `python -m src.proposer` (with key set) OR fallback line without a key | `09_proposer.png` |
| 10 | Candidate validation | the `[5] Validating candidate... [PASS]` line in the demo | `10_validate.png` |
| 11 | End-to-end pipeline (M10) | `python -m src.pipeline --demo` | `11_pipeline.png` |
| 12 | Rule distillation (M9) | the `[10]` line of the demo + `Get-Content rules\rules.json` | `12_rules.png` |
| 13 | wasm-opt baseline (M8) | `python -m src.baseline examples\x_plus_zero.wasm examples\x_times_two.wasm` | `13_baseline.png` |
| 14 | pytest results | `pytest -v` | `14_pytest.png` |
| 15 | results CSV/JSON | `python -m src.experiment` then open `results\results.csv` | `15_results.png` |
| 16 | Final demo (rule reuse) | `.\scripts\run_demo.ps1` (run 2 shows `[RULE HIT]`) | `16_demo.png` |

Tip for #16: the strongest single screenshot is run 2 of the demo, where the same block is
optimized with `[RULE HIT]` and no LLM call — concrete proof the distillation loop closes.
