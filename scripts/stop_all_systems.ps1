# ============================================================
#  TC Platform - STOP ALL SYSTEMS
#  Stops the platform and all four existing apps that were
#  started by start_all_systems.ps1.
# ============================================================
Write-Host "Stopping TC Platform and all systems..." -ForegroundColor Cyan

$patterns = @('run.py', 'app.py', '_launch_asset.py', '_launch_monitoring.py')
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'"
$stopped = 0
foreach ($p in $procs) {
    foreach ($pat in $patterns) {
        if ($p.CommandLine -and $p.CommandLine -like "*$pat*") {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host ("  stopped PID {0}  ({1})" -f $p.ProcessId, $pat) -ForegroundColor Yellow
            $stopped++
            break
        }
    }
}
if ($stopped -eq 0) { Write-Host "  nothing was running." -ForegroundColor DarkGray }
Write-Host "Done." -ForegroundColor Green
