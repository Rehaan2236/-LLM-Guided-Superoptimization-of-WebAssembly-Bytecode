# verify_environment.ps1 — check every tool is installed and importable.
# Run from the project root with the venv active.
Write-Host "=== Versions ===" -ForegroundColor Cyan
python --version
git --version
wat2wasm --version
wasm2wat --version
wasm-opt --version

Write-Host "`n=== Python packages ===" -ForegroundColor Cyan
python -c "import z3; print('Z3:', z3.get_version_string())"
python -c "import wasmtime; print('Wasmtime OK')"
python -c "from google import genai; print('Gemini SDK OK')"

Write-Host "`n=== Gemini key present? ===" -ForegroundColor Cyan
if ($env:GEMINI_API_KEY) { Write-Host "GEMINI_API_KEY is set (value hidden)" }
else { Write-Host "GEMINI_API_KEY not set — pipeline will use the manual fallback" -ForegroundColor Yellow }
