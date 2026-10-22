@echo off
REM Market monitor (scripts/market_monitor.py): every 30 min, 24/5 (operator 2026-10-22). Outside regular hours only the :45 pass runs (Gemma's sell-ifs).
REM Market alarm sells the operator's picks; Gemma's concern sells that pick; hourly (:45) Gemma
REM re-checks her own sell-ifs and may sell. Operator's choices 2026-10-22, logged in FREEZE.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_monitor.log
echo ===== MONITOR %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\market_monitor.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
