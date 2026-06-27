# ============================================================
#  TC Platform - health check for the platform + all systems
# ============================================================
$platform = if ($env:TC_PLATFORM_URL) { $env:TC_PLATFORM_URL } else { "http://127.0.0.1:7000" }

$targets = [ordered]@{
    "TC Platform"       = "$platform/api/health"
    "ITSM Service Desk" = "http://127.0.0.1:5000/health"
    "Asset Management"  = "http://127.0.0.1:5001/api/health"
    "Monitoring"        = "http://127.0.0.1:5002/health"
    "CommandTrack"      = "http://127.0.0.1:5003/"
}

Write-Host ""
Write-Host "  TC Platform - Health Check" -ForegroundColor Cyan
Write-Host "  ==========================" -ForegroundColor Cyan

foreach ($name in $targets.Keys) {
    $url = $targets[$name]
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 4
        $sw.Stop()
        Write-Host ("  [ ONLINE ] {0,-20} {1,4} ms  {2}" -f $name, $sw.ElapsedMilliseconds, $url) -ForegroundColor Green
    } catch {
        $sw.Stop()
        Write-Host ("  [OFFLINE ] {0,-20}          {1}" -f $name, $url) -ForegroundColor Red
    }
}
Write-Host ""
