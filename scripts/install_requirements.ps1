# ============================================================
#  TC Platform - install dependencies
# ============================================================
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host "TC Platform - installing requirements..." -ForegroundColor Cyan

# Create a virtual environment if missing (recommended)
if (-not (Test-Path "$root\.venv")) {
    Write-Host "Creating virtual environment (.venv)..." -ForegroundColor Yellow
    python -m venv .venv
}

$py = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }   # fall back to system python

& $py -m pip install --upgrade pip
& $py -m pip install -r "$root\requirements.txt"

# Copy .env if missing
if (-not (Test-Path "$root\.env")) {
    Copy-Item "$root\.env.example" "$root\.env"
    Write-Host "Created .env from .env.example - review TC_SECRET_KEY and TC_ADMIN_PASSWORD." -ForegroundColor Yellow
}

Write-Host "Done. Start the platform with:  .\scripts\start_platform.ps1" -ForegroundColor Green
