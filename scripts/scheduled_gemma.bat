@echo off
REM The rules book's 9:45 ET pick, 05:45 AK weekdays (scripts/rules_pick.py). No model is asked:
REM S&P 500 names passing the buy checks, strongest 12-1 momentum first, queued for the 10:00 buy.
REM Operator 2026-10-26: "stop Gemma from picking". Task name kept (stock-scanner-gemma).
REM Own log (WORKLIST §40): never share a log with another task.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_rules_pick.log
echo. >> "%LOG%"
echo ===== RULES PICK %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\rules_pick.py" --submit >> "%LOG%" 2>&1
exit /b %errorlevel%
