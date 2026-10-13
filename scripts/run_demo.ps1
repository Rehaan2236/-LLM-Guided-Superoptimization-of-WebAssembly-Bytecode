# run_demo.ps1 — the 3-5 minute professor demo.
# Shows the x+0 pipeline end to end, then proves rule reuse on a second run.
$ErrorActionPreference = "Stop"
if (-not (Test-Path "examples\x_plus_zero.wasm")) {
    wat2wasm examples\x_plus_zero.wat -o examples\x_plus_zero.wasm
}
'[]' | Set-Content rules\rules.json

Write-Host "`n################  RUN 1: full pipeline (LLM or manual fallback)  ################" -ForegroundColor Cyan
python -m src.pipeline --demo

Write-Host "`n################  RUN 2: same block — verified rule is reused, no LLM  ##########" -ForegroundColor Cyan
python -m src.pipeline --demo

Write-Host "`n################  Stored rule  ################" -ForegroundColor Cyan
Get-Content rules\rules.json
