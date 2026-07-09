@echo off
REM TC Platform - run the full backend QA suite (Windows)
setlocal
cd /d "%~dp0"
echo ============================================================
echo  TC Platform - QA automated tests
echo ============================================================
python -m pytest -q -p no:cacheprovider --ignore=tests/e2e
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (echo RESULT: ALL BACKEND TESTS PASSED) else (echo RESULT: FAILURES - see output above)
echo (E2E UI tests: run "python -m pytest tests/e2e" with Chrome + Playwright installed.)
exit /b %RC%
