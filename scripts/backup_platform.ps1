# ============================================================
#  TC Platform - back up the platform metadata database
# ============================================================
$root = Split-Path $PSScriptRoot -Parent
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = Join-Path $root "backups\$stamp"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$db = Join-Path $root "platform.db"
if (Test-Path $db) {
    Copy-Item $db (Join-Path $backupDir "platform.db")
    Write-Host "Backed up platform.db -> $backupDir" -ForegroundColor Green
} else {
    Write-Host "platform.db not found (will be created on first run)." -ForegroundColor Yellow
}

# Keep a copy of .env too (without committing it anywhere)
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) { Copy-Item $envFile (Join-Path $backupDir ".env.bak") }

Write-Host "Backup complete: $backupDir" -ForegroundColor Cyan
