# compile_examples.ps1 — compile every examples\*.wat to .wasm with wat2wasm.
$ErrorActionPreference = "Stop"
Get-ChildItem examples\*.wat | ForEach-Object {
    $out = $_.FullName -replace '\.wat$', '.wasm'
    wat2wasm $_.FullName -o $out
    Write-Host "compiled $($_.Name)"
}
