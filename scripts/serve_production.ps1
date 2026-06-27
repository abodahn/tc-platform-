# ============================================================
#  TC Platform - run with a PRODUCTION WSGI server (waitress)
# ============================================================
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$py = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

# Ensure waitress is installed
& $py -m pip install -q waitress 2>$null

$env:TC_ENV = "production"
if (-not $env:TC_PORT) { $env:TC_PORT = "7000" }
if (-not $env:TC_SECRET_KEY) {
    Write-Host "WARNING: TC_SECRET_KEY not set - a persistent key file will be used." -ForegroundColor Yellow
}

Write-Host "Starting TC Platform (production/waitress) on port $($env:TC_PORT) ..." -ForegroundColor Cyan
& $py "$root\serve.py"
