@echo off
REM Morning ideas (scripts/morning_ideas.py), 04:40 AK weekdays: earnings winners and
REM estimates rising, S&P 500 + MidCap 400. REPORT ONLY: no orders, picks.txt untouched,
REM Gemma does not see it. Rules in scanner/ops/ideas.py. Own log (WORKLIST §40).

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_ideas.log
echo. >> "%LOG%"
echo ===== IDEAS %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\morning_ideas.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
