@echo off
REM Gemma's book outside regular hours (24/5, operator 2026-10-22): every 2 hours,
REM 13:15-03:15 AK -- after-hours, overnight, premarket. Same decision as the 05:45 run;
REM orders go as limit orders and protect.py arms the stops at the open. No session open
REM (Friday night, weekends) -> exits before asking Gemma. Own log (WORKLIST §40).
REM News: the daily brief is written 03:00, 12:50 and 18:50 AK (stock-scanner-weekahead);
REM she reads the newest within 12 h (advisor/brief_store.py).

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_gemma_ext.log
echo. >> "%LOG%"
echo ===== GEMMA EXT %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\gemma_trade.py" --submit --only-when-tradable >> "%LOG%" 2>&1
exit /b %errorlevel%
