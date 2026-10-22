@echo off
REM Gemma's book outside regular hours (24/5, operator 2026-10-22): every 2 hours,
REM 13:15-03:15 AK -- after-hours, overnight, premarket. Same decision as the 05:45 run;
REM orders go as limit orders and protect.py arms the stops at the open. No session open
REM (Friday night, weekends) -> exits before asking Gemma. Own log (WORKLIST §40).
REM First the daily brief is rebuilt (scripts/week_ahead.py, ~35 s): the 03:00 one is
REM 10-20 hours old by evening, and after midnight AK there is none for the new date.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_gemma_ext.log
echo. >> "%LOG%"
echo ===== GEMMA EXT %DATE% %TIME% ===== >> "%LOG%"

echo ----- BRIEF %TIME% ----- >> "%LOG%"
".venv\Scripts\python.exe" "scripts\week_ahead.py" >> "%LOG%" 2>&1
echo ----- DECISION %TIME% ----- >> "%LOG%"
".venv\Scripts\python.exe" "scripts\gemma_trade.py" --submit --only-when-tradable >> "%LOG%" 2>&1
exit /b %errorlevel%
