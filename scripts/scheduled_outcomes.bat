@echo off
REM Append what actually happened to each closed trade.
REM
REM READ-MOSTLY AND OUTSIDE THE FROZEN TRADING PATH. It reads the broker and
REM appends trade_outcome records. It places no order, cancels nothing and
REM touches no policy, so it cannot influence a trading decision and running it
REM does not end an evaluation window.
REM
REM 13:30 AK, half an hour after the data collector, an hour and a half after
REM the close. Late enough that the day's fills have settled at the broker.
REM
REM Idempotent by design: outcomes already in the log are skipped, so a missed
REM day is picked up by the next run and a double run writes nothing twice.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled.log
echo. >> "%LOG%"
echo ===== OUTCOMES %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\record_outcomes.py" --write >> "%LOG%" 2>&1
exit /b %errorlevel%
