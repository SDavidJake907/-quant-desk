@echo off
REM The health check, 06:00 and 14:15 AK weekdays (scripts/health_check.py, ops/health.py;
REM operator 2026-10-20 "lets monitor all on schedule"). READ-ONLY: stops on every position,
REM every task's last run, Ollama orphans, today's logs, Hindsight/Gemma/review outputs.
REM Report in logs\health\YYYY-MM-DD.md; problems pop up on this PC (and phone, if ntfy is set).
REM Own log (WORKLIST 40).

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_health.log
echo. >> "%LOG%"
echo ===== HEALTH %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\health_check.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
