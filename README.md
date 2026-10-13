# LLM-Guided Superoptimization of WebAssembly Bytecode

A verify-gated superoptimizer for WebAssembly integer basic blocks. An LLM (Gemini)
proposes shorter instruction sequences; the system accepts a proposal only after it
passes two independent correctness gates. The LLM is never trusted on its own.

This is a small, real, runnable prototype for a Compiler Design project (Review 2),
built for Windows 11 + VS Code. Follow this README top to bottom.

---

## 1. Project overview

WebAssembly (WASM) runs in browsers and cloud apps. Smaller, cheaper WASM loads faster
and costs less to run. Normal compilers optimize with a fixed rulebook, so they miss
clever rewrites that aren't in it.

This project uses an LLM as a *creative candidate generator*: it proposes potentially
cheaper, equivalent WASM instruction sequences. But LLM output can be wrong, so every
candidate must clear two gates:

1. **Differential testing** — run original and candidate on many inputs; reject obvious
   mismatches. Fast, but evidence only.
2. **Z3 (SMT) verification** — mathematically prove the two behave identically for *every*
   input under the modeled semantics. Only rewrites that pass this are accepted.

When the same verified rewrite recurs, it is distilled into a reusable peephole rule so
future blocks skip the LLM and the proof.

**Version 1 scope:** i32 only, straight-line blocks, no branches/loops/memory/floats/calls,
no trapping instructions, 1–3 parameters, ≤12 instructions, exactly one i32 result.

**Version 2 (added):** ten more non-trapping i32 opcodes — `i32.rotl`, `i32.rotr`, and the
signed/unsigned comparisons (`lt_s/lt_u/gt_s/gt_u/le_s/le_u/ge_s/ge_u`). These widen the
search enough to find rewrites Binaryen `wasm-opt -Oz` does not perform. See
`docs/NOVELTY_RESULTS.md` and run `python -m src.superopt_search`.

### Novelty result (reproducible)

`src/superopt_search.py` is an enumerative superoptimizer that discovers shorter-equivalent
rewrites, proves each with Z3, and checks them against `wasm-opt -Oz`. It finds genuine
misses, e.g. `(x ^ y) == 0` → `x == y` and the sign-bit test `(x >> 31) == 0` → `x >= 0`.
Run the live pipeline on one:

```powershell
python -m src.pipeline examples\xor_eqz.wasm
```

It accepts a Z3-verified 4→3 rewrite that wasm-opt leaves at 4 instructions.

---

## 2. Architecture

```
WASM
 ↓  Basic-block extraction        (src/extractor.py, M3)
 ↓  DSL                           (src/wasm_translator.py, M4)
 ↓  Rule lookup                   (src/distiller.py, M9)   -> reuse if hit
 ↓  Gemini candidate generation   (src/proposer.py, M5)    -> manual fallback offline
 ↓  Validation                    (src/dsl.py)
 ↓  Differential testing          (src/difftest.py, M6)    -> fast filter
 ↓  Z3 verification               (src/verifier.py, M7)    -> proof
 ↓  Verified rewrite              (accept shortest that verifies)
 ↓  Rule distillation             (src/distiller.py, M9)
```

Baseline comparison against Binaryen `wasm-opt` is `src/baseline.py` (M8); the full
pipeline is `src/pipeline.py` (M10). See `docs/ARCHITECTURE.md` for details.

---

## 3. Requirements

- Windows 11
- VS Code
- Git
- Python 3.12
- WABT (`wat2wasm`, `wasm2wat`)
- Binaryen (`wasm-opt`)
- Z3 (Python package)
- Wasmtime (Python package)
- Gemini API key (optional — the pipeline runs without it using a manual fallback)

Put the project at `C:\dev\wasm-superoptimizer` and open it in VS Code:

```powershell
cd C:\dev\wasm-superoptimizer
code .
```

Use a PowerShell terminal (VS Code: Terminal → New Terminal, set to PowerShell).
After any PATH change, close and reopen the terminal.

---

## 4. Install Python and Git

```powershell
python --version
git --version
```

If either is missing:

```powershell
winget install --id Python.Python.3.12 -e
winget install --id Git.Git -e
```

Reopen the terminal and re-check. If `python` opens the Microsoft Store, go to
Settings → Apps → Advanced app settings → App execution aliases and turn off the
`python.exe` / `python3.exe` aliases.

---

## 5. Create the virtual environment

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Your prompt should now start with `(.venv)`. If activation is blocked:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
```

In VS Code: Ctrl+Shift+P → "Python: Select Interpreter" → pick `.venv`.

---

## 6. Install Python dependencies

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

This installs `z3-solver`, `wasmtime`, `google-genai`, and `pytest`.
(`scripts\setup.ps1` does steps 5–6 in one go.)

---

## 7. Install WABT (wat2wasm, wasm2wat)

1. Open https://github.com/WebAssembly/wabt/releases and download the latest asset
   ending in `-windows.tar.gz`.
2. Extract it to `C:\tools` (Windows 11 ships `tar`):

   ```powershell
   mkdir C:\tools -Force
   tar -xzf $HOME\Downloads\wabt-*-windows.tar.gz -C C:\tools
   Rename-Item C:\tools\wabt-* wabt
   Test-Path C:\tools\wabt\bin\wat2wasm.exe
   ```

---

## 8. Install Binaryen (wasm-opt)

1. Open https://github.com/WebAssembly/binaryen/releases and download the latest asset
   ending in `x86_64-windows.tar.gz`.
2. Extract and rename:

   ```powershell
   tar -xzf $HOME\Downloads\binaryen-*-windows.tar.gz -C C:\tools
   Rename-Item C:\tools\binaryen-version_* binaryen
   Test-Path C:\tools\binaryen\bin\wasm-opt.exe
   ```

### Add both to PATH (user-level, no admin)

```powershell
$p = [Environment]::GetEnvironmentVariable("Path", "User")
[Environment]::SetEnvironmentVariable("Path", "$p;C:\tools\wabt\bin;C:\tools\binaryen\bin", "User")
```

Close and reopen the terminal, `cd` back to the project, and re-activate `.venv`.
(Wasmtime needs no separate install — it's the Python package from step 6.)

---

## 9. Verify the environment

```powershell
python --version
git --version
wat2wasm --version
wasm2wat --version
wasm-opt --version

python -c "import z3; print('Z3:', z3.get_version_string())"
python -c "import wasmtime; print('Wasmtime OK')"
python -c "from google import genai; print('Gemini SDK OK')"
```

`scripts\verify_environment.ps1` runs all of these at once. Every line should print a
version or `OK`.

---

## 10. Configure the Gemini API (optional)

Create a key at https://aistudio.google.com/apikey. Copy `.env.example` to `.env` and
paste the key:

```powershell
Copy-Item .env.example .env
notepad .env      # set GEMINI_API_KEY=your-key-here
```

`.env` is git-ignored. The code reads `.env` automatically when it starts, so you
don't need to set anything in the terminal. The key is never hard-coded or printed.
The model name is set in `config.json` (`"gemini_model": "gemini-2.5-flash"`).

Without a key, everything still works via the manual fallback.

---

## 11. Compile the first example

```powershell
wat2wasm examples\x_plus_zero.wat -o examples\x_plus_zero.wasm
```

Compile all examples at once: `.\scripts\compile_examples.ps1`

---

## 12. Run the extractor (M3)

```powershell
python -m src.extractor examples\x_plus_zero.wasm
```

Expect JSON with `"skipped": false` and three instructions (`local.get`, `i32.const`,
`i32.add`).

---

## 13. See the DSL conversion (M4)

```powershell
python -c "from src.extractor import first_supported_block as f; from src.wasm_translator import wasm_instrs_to_dsl as g; from src.dsl import serialize; print(serialize(g(f('examples/x_plus_zero.wasm')['instructions'])))"
```

Expect `['PUSH_INPUT 0', 'PUSH_CONST 0', 'ADD']`.

---

## 14. Run the Z3 verifier (M7)

```powershell
python -m src.verifier
```

Expect five lines, each marked `[OK]`: x+0 EQUIVALENT (unsat), x+1 NOT_EQUIVALENT (sat),
x*2 ≡ x+x, x+1 vs x+2 NOT_EQUIVALENT, x<<40 ≡ x<<8.

---

## 15. Run differential testing (M6)

```powershell
python -m src.difftest
```

Expect x+0 vs x to pass all sampled inputs, and x+0 vs x+2 to fail with a counterexample.

---

## 16. Run the Gemini proposer (M5)

```powershell
python -m src.proposer
```

With a key set, it returns Gemini candidates (cached after the first call). Without a key
it prints manual-fallback candidates. Either way every candidate still has to pass M6 + M7.

---

## 17. Run the complete pipeline (M10)

```powershell
python -m src.pipeline examples\x_plus_zero.wasm
```

Prints the staged `[1]…[10]` trace, ending in ACCEPTED, 3→1, rule stored. Force the
offline path with `--no-gemini`.

---

## 18. Run the tests

```powershell
pytest -v
```

43 tests. Integration tests needing WABT/Wasmtime auto-skip if those tools are absent.

---

## 19. Run the baseline (M8)

```powershell
python -m src.baseline examples\x_plus_zero.wasm examples\x_times_two.wasm
```

Writes `results\baseline.json` and `results\baseline.csv`. On x+0, `wasm-opt` also reduces
to 1 instruction — this proves the pipeline works, not that it beats `wasm-opt`.

Generate the aggregate initial results:

```powershell
python -m src.experiment
```

---

## 20. Run the demo

```powershell
python -m src.pipeline --demo
```

or the two-run script that also proves rule reuse:

```powershell
.\scripts\run_demo.ps1
```

The demo output:

```
[1] Extracting basic block
[2] Translating to DSL
[3] Checking existing rules
[4] Generating candidate        (or: [LLM unavailable — using manual demonstration candidate])
[5] Validating candidate
[6] Differential testing
[7] Z3 verification
[8] Candidate ACCEPTED
[9] Instruction reduction  (3 -> 1)
[10] Rule stored
```

Run 2 shows `[RULE HIT]` and skips the LLM entirely. No output is faked; every verdict
comes from the modules. The demo never requires Gemini.

---

## 21. Troubleshooting

| Symptom | Fix |
|---|---|
| `python` not found | Reopen terminal after install; disable Store aliases (section 4). |
| venv won't activate | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again. |
| PowerShell "running scripts is disabled" | Same as above. |
| `wat2wasm` not found | Folder must be exactly `C:\tools\wabt`; reopen terminal after PATH change. |
| `wasm2wat` not found | Same WABT bin folder; check `Test-Path C:\tools\wabt\bin\wasm2wat.exe`. |
| `wasm-opt` not found | Folder must be exactly `C:\tools\binaryen`; reopen terminal. |
| Missing-DLL from WABT/Binaryen | Install Microsoft Visual C++ Redistributable (x64). |
| `import z3` fails / builds from source | Use Python 3.12 so prebuilt wheels are available: `pip install z3-solver`. |
| Wasmtime import/exec error | `pip install --force-reinstall wasmtime`; ensure 64-bit Python. |
| Gemini 403 / missing key | Key not in this terminal: reopen it; `echo $env:GEMINI_API_KEY`. The pipeline still runs without a key. |
| Invalid JSON from Gemini | Handled: the proposer retries and parses fenced/noisy replies; it falls back to manual candidates. |
| API rate limits | Responses are cached under `cache\`; rerun uses the cache. Use `--no-gemini` to avoid calls. |
| Z3 timeout / unknown | Treated as UNVERIFIED and rejected. Raise `z3_timeout_ms` in `config.json` if needed. |
| Invalid WASM | `wat2wasm` prints the error; fix the `.wat` and recompile. |
| Invalid DSL / stack error | `dsl.validate` names the exact problem (underflow, bad index, multiple results). |
| Windows path errors | Code uses `pathlib`; always run commands from the project root. |

---

## Project layout

```
wasm-superoptimizer/
├── README.md  requirements.txt  requirements.lock.txt  .gitignore  .env.example  config.json
├── src/        dsl, wasm_translator, extractor, verifier, difftest, proposer,
│               distiller, baseline, pipeline, experiment
├── tests/      pytest suite (unit + integration)
├── examples/   x_plus_zero, x_plus_one, x_times_two, x_times_one, x_times_zero,
│               x_minus_zero, x_or_zero, x_xor_zero, double_add  (.wat + .wasm)
├── rules/      rules.json (verified peephole rules)
├── corpus/     your own larger .wasm inputs for experiments
├── cache/      cached LLM responses (git-ignored)
├── results/    baseline + experiment outputs (json/csv)
├── docs/       ARCHITECTURE, METHODOLOGY, TESTING, REVIEW_2_EVIDENCE, SCREENSHOT_CHECKLIST
└── scripts/    setup.ps1, verify_environment.ps1, compile_examples.ps1, run_demo.ps1
```

## Honesty notes (for the viva)

- The x+0 reduction is also found by `wasm-opt`. It demonstrates the pipeline, not novelty.
- Equivalence holds only within the modeled i32, trap-free, straight-line semantics.
- The defensible contribution is the *combination* — an LLM proposer on WebAssembly with a
  differential-test-then-Z3 gate and rule distillation — not a first-of-its-kind claim.
  Any "finds rewrites wasm-opt misses" claim must come from experiments on a real corpus.x
## Web UI (local front end)

A three-panel UI (Input → Processing → Output) over the same pipeline. No extra dependencies.

```powershell
.\scripts\run_ui.ps1          # or: python -m app.server  (then open http://127.0.0.1:8000)
```

Pick an example or paste WAT / upload a `.wasm`, optionally tick *Use Gemini*, and press Optimize.
The middle panel shows each pipeline stage with PASS/FAIL; the right panel shows the verdict,
instruction counts vs `wasm-opt -Oz`, and original vs optimized WAT/DSL. *Reset rules* clears
`rules/rules.json` so the next run is a cold (non-rule-hit) run.
