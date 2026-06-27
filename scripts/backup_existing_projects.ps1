# ============================================================
#  TC Platform - SAFE backup of the existing T&C systems
#  Copies each existing project into a timestamped backup folder
#  BEFORE any change. Excludes heavy folders (.venv, __pycache__,
#  node_modules) to keep the backup fast and small. NON-DESTRUCTIVE.
# ============================================================
$ErrorActionPreference = "Stop"

# Parent folder that contains the existing systems (one level above tc-platform)
$workspace = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$dest = Join-Path $workspace "_BACKUP_existing_systems_$stamp"
New-Item -ItemType Directory -Force -Path $dest | Out-Null

$projects = @(
    "TC_Garments_ExcelDB_FINAL",
    "enterprise_asset_inventory",
    "windows-monitoring-command-center",
    "TC_CommandTrack_PREMIUM_ENTERPRISE_TRILINGUAL_v2_3_1"
)
$exclude = @(".venv", "venv", "__pycache__", "node_modules", ".git")

Write-Host "Backing up existing systems to:" -ForegroundColor Cyan
Write-Host "  $dest" -ForegroundColor Cyan

foreach ($p in $projects) {
    $src = Join-Path $workspace $p
    if (Test-Path $src) {
        Write-Host "  Copying $p ..." -ForegroundColor Yellow
        $rcArgs = @($src, (Join-Path $dest $p), "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
        foreach ($x in $exclude) { $rcArgs += "/XD"; $rcArgs += $x }
        robocopy @rcArgs | Out-Null
    } else {
        Write-Host "  (skip) $p not found" -ForegroundColor DarkGray
    }
}
Write-Host "Backup complete. Existing systems are untouched." -ForegroundColor Green
