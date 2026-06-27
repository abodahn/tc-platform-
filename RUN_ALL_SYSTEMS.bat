@echo off
title TC Platform - RUN ALL SYSTEMS
cd /d "%~dp0"
echo.
echo  ============================================================
echo    TC Platform - starting EVERYTHING (no popup windows)
echo  ============================================================
echo    All systems run in the background and log to .\logs
echo    One combined LOG window will open.
echo.

REM Start the platform + all four systems (hidden, logged)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_all_systems.ps1"

REM Open ONE combined live-log window for all systems
start "TC Systems Log" powershell -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\tail_logs.ps1"

echo.
echo  Done. The platform is opening in your browser.
echo  A single combined LOG window has opened (close it any time - apps keep running).
echo  To STOP everything later, run:  scripts\stop_all_systems.ps1
echo.
echo  You can close THIS window now.
timeout /t 5 >nul
