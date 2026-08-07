@echo off
REM Scheduled market-hours scan. Wrapped rather than called directly because
REM config paths (logs/, data/) are relative to the project root, and a task
REM started from C:\Windows\System32 would write its audit log somewhere nobody
REM ever looks — the classic "works by hand, silently wrong on a schedule" bug.
REM
REM trade.py refuses to run when the market is closed, so weekends and holidays
REM cost one process start and nothing else.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled.log
echo. >> "%LOG%"
echo ===== %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\trade.py" --submit-paper >> "%LOG%" 2>&1
exit /b %errorlevel%
