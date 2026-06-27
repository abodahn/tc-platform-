@echo off
title TC Platform - T&C Digital Operations Platform
cd /d "%~dp0"

REM --- Pick the virtual-env Python if present, else system Python ---
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM --- First run: create .env from the template ---
if not exist ".env" copy ".env.example" ".env" >nul

REM --- Make sure dependencies are installed (quiet, safe every run) ---
echo Checking dependencies...
"%PY%" -m pip install -q -r requirements.txt

echo.
echo ============================================================
echo    TC Platform is starting...
echo    URL:   http://127.0.0.1:7000
echo    Login: admin  /  Admin@12345
echo.
echo    Keep this window open. Press Ctrl+C to stop.
echo ============================================================
echo.

REM --- Open the browser a few seconds after the server starts ---
start "" cmd /c "timeout /t 4 >nul & start "" http://127.0.0.1:7000"

REM --- Start the platform (this blocks until you stop it) ---
"%PY%" run.py

echo.
echo TC Platform stopped.
pause
