# ============================================================
#  TC Platform - start the platform
# ============================================================
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$py = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

# Default development port
if (-not $env:TC_PORT) { $env:TC_PORT = "7000" }

Write-Host "Starting TC Platform on http://127.0.0.1:$($env:TC_PORT) ..." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray
& $py "$root\run.py"
