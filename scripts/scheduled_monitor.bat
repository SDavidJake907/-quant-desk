@echo off
REM Market-hours monitor (scripts/market_monitor.py): every 30 min 06:15-11:45 AK (10:15-15:45 ET).
REM Market alarm sells the operator's picks; Gemma's concern sells that pick; hourly (:45) Gemma
REM re-checks her own sell-ifs and may sell. Operator's choices 2026-10-22, logged in FREEZE.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_monitor.log
echo ===== MONITOR %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\market_monitor.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
