@echo off
REM The Friday weekly review, 07:00 AK: every open position and book against the
REM S&P 500, saved to logs\weekly\YYYY-MM-DD.md. Read-only. WORKLIST §45.
REM Own log (WORKLIST §40).

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_weekly.log
echo. >> "%LOG%"
echo ===== WEEKLY %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\weekly_report.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
