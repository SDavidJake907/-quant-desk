@echo off
REM Gemma's evening review (scripts/gemma_review.py), 12:30 AK weekdays, after the close.
REM Trades nothing: writes data/gemma/reviews/ for the dashboard's review panel and Ask Gemma.
REM Took TradingAgents' place (paused 2026-10-19, operator). Own log (WORKLIST §40).

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_gemma_review.log
echo. >> "%LOG%"
echo ===== GEMMA REVIEW %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\gemma_review.py" >> "%LOG%" 2>&1
set RC=%errorlevel%

REM Then her safety check of the OPERATOR's positions (scripts/operator_watch.py, operator
REM 2026-10-21): advice only, after the review so one model runs at a time. Its own
REM failure never changes this task's result, which stays the review's.
echo ----- OPERATOR WATCH %TIME% ----- >> "%LOG%"
".venv\Scripts\python.exe" "scripts\operator_watch.py" >> "%LOG%" 2>&1
exit /b %RC%
