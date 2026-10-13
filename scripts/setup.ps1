# setup.ps1 — one-time project setup on Windows 11 (PowerShell).
# Run from the project root:  .\scripts\setup.ps1
$ErrorActionPreference = "Stop"

Write-Host "=== Creating virtual environment ===" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { python -m venv .venv }
.\.venv\Scripts\Activate.ps1

Write-Host "=== Installing Python dependencies ===" -ForegroundColor Cyan
pip install --upgrade pip
pip install -r requirements.txt

Write-Host "=== Done. Next: install WABT + Binaryen (see README sections 7-8), then run scripts\verify_environment.ps1 ===" -ForegroundColor Green
