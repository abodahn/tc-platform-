# ============================================================
#  TC Platform - Combined live log for ALL systems
#  One window that streams the output of every system together,
#  each line prefixed and colour-coded by system.
#
#  Reads through an OPEN shared stream (not the directory size),
#  because Windows reports a stale 0-byte size while a process
#  holds the log file open.
#
#  Closing this window does NOT stop the apps (they run hidden).
#  To stop everything: scripts\stop_all_systems.ps1
# ============================================================
$tp = Split-Path $PSScriptRoot -Parent
$logs = Join-Path $tp "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

$colors = @{ itsm = 'Cyan'; asset = 'Green'; monitoring = 'Magenta'; commandtrack = 'Yellow'; platform = 'White' }
$readers = @{}

function Get-Reader($path) {
    if (-not $readers.ContainsKey($path)) {
        try {
            $fs = [System.IO.File]::Open($path, [System.IO.FileMode]::Open,
                   [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
            $readers[$path] = New-Object System.IO.StreamReader($fs)
        } catch { return $null }
    }
    return $readers[$path]
}

Clear-Host
Write-Host "============================================================" -ForegroundColor Red
Write-Host "  TC Platform - Combined Systems Log" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Red
Write-Host "  itsm=Cyan  asset=Green  monitoring=Magenta  commandtrack=Yellow  platform=White"
Write-Host "  Folder : $logs"
Write-Host "  Closing this window does NOT stop the systems."
Write-Host "  To STOP all systems: scripts\stop_all_systems.ps1"
Write-Host "------------------------------------------------------------`n"

while ($true) {
    foreach ($file in (Get-ChildItem $logs -Filter *.log -ErrorAction SilentlyContinue)) {
        $name = ($file.BaseName -replace '\.(out|err)$', '')
        $r = Get-Reader $file.FullName
        if (-not $r) { continue }
        # handle truncation on restart
        if ($r.BaseStream.Length -lt $r.BaseStream.Position) {
            $r.BaseStream.Seek(0, 'Begin') | Out-Null
            $r.DiscardBufferedData()
        }
        $chunk = $r.ReadToEnd()
        if ($chunk -and $chunk.Length -gt 0) {
            $color = $colors[$name]; if (-not $color) { $color = 'Gray' }
            foreach ($line in ($chunk -split "`r?`n")) {
                if ($line.Trim().Length -gt 0) {
                    Write-Host ("[{0,-12}] " -f $name) -ForegroundColor $color -NoNewline
                    Write-Host $line
                }
            }
        }
    }
    Start-Sleep -Milliseconds 700
}
