# ============================================================
#  TC Platform - stop the platform (frees the dev port)
# ============================================================
$port = if ($env:TC_PORT) { $env:TC_PORT } else { "7000" }
Write-Host "Stopping any TC Platform process on port $port ..." -ForegroundColor Cyan

$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
         Where-Object { $_.CommandLine -like '*run.py*' }
foreach ($p in $procs) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "  Stopped PID $($p.ProcessId)" -ForegroundColor Yellow
}

# Also free the port if held by another process
$conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conns) {
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "  Freed port $port (PID $($c.OwningProcess))" -ForegroundColor Yellow
}
Write-Host "Done." -ForegroundColor Green
