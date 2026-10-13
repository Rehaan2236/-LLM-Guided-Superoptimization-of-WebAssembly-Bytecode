# run_ui.ps1 — start the local web UI at http://127.0.0.1:8000
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
Start-Process "http://127.0.0.1:8000"
& .\.venv\Scripts\python.exe -m app.server
