# ============================================================
#  TC Platform - START ALL SYSTEMS (single launcher, no popups)
#  Starts the platform + all four existing systems HIDDEN (no
#  console windows). All output is written to .\logs\*.log and
#  shown together in one combined log window (tail_logs.ps1).
#  Non-destructive: every app uses create-if-not-exists on its DB.
# ============================================================
$ErrorActionPreference = "Continue"
$tp = Split-Path $PSScriptRoot -Parent        # tc-platform
$ws = Split-Path $tp -Parent                  # workspace (contains all systems)

$py = Join-Path $tp ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$logs = Join-Path $tp "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

# Run the platform as a single clean process (no debug reloader / double process)
$env:TC_DEBUG = "false"

# Stop anything already running so ports are free (safe on a fresh start)
& "$PSScriptRoot\stop_all_systems.ps1" | Out-Null
Start-Sleep -Seconds 1

Write-Host "Installing / verifying dependencies (one time)..." -ForegroundColor Cyan
& $py -m pip install -q Flask==3.0.3 Werkzeug==3.0.3 openpyxl==3.1.5 python-dotenv==1.0.1 `
    requests Flask-SQLAlchemy==3.1.1 itsdangerous==2.2.0 qrcode Pillow reportlab `
    Flask-SocketIO==5.3.6 python-socketio==5.11.3 python-engineio==4.9.1 2>$null

$itsm = Join-Path $ws "TC_Garments_ExcelDB_FINAL\TC_Garments_ExcelDB_FINAL"
$ct   = Join-Path $ws "TC_CommandTrack_PREMIUM_ENTERPRISE_TRILINGUAL_v2_3_1\TC_CommandTrack_PREMIUM_ENTERPRISE_TRILINGUAL_v2_3_1"

function Launch($name, $dir, $argList) {
    if (-not (Test-Path $dir)) { Write-Host "  (skip) $name - folder not found" -ForegroundColor DarkGray; return }
    $out = Join-Path $logs "$name.out.log"
    $err = Join-Path $logs "$name.err.log"
    # truncate previous logs
    Set-Content -Path $out -Value "" -NoNewline; Set-Content -Path $err -Value "" -NoNewline
    Start-Process -FilePath $py -ArgumentList $argList -WorkingDirectory $dir `
        -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err
    Write-Host ("  started {0}" -f $name) -ForegroundColor Green
}

Write-Host "Starting all systems (hidden, logging to .\logs)..." -ForegroundColor Cyan
# Note: helper-script paths contain a space, so they MUST be passed quoted.
Launch "itsm"         $itsm @('app.py')
Launch "asset"        $tp   @("`"$tp\scripts\_launch_asset.py`"")
Launch "monitoring"   $tp   @("`"$tp\scripts\_launch_monitoring.py`"")
Launch "commandtrack" $ct   @('app.py','--port=5003','--host=127.0.0.1')
Launch "platform"     $tp   @('run.py')

Write-Host ""
Write-Host "  TC PLATFORM      http://127.0.0.1:7000   <-- open this" -ForegroundColor Yellow
Write-Host "  ITSM 5000  Asset 5001  Monitoring 5002  CommandTrack 5003" -ForegroundColor DarkGray
Write-Host "  Opening the platform in your browser in ~8s..." -ForegroundColor Cyan
Start-Sleep -Seconds 8
Start-Process "http://127.0.0.1:7000"
