@echo off
REM Peak watch (scripts/peak_watch.py): every 5 min 05:30-12:00 AK (09:30-16:00 ET).
REM READY = in profit + >= 4 of 6 peak checks; SELL = a READY position whose 5-minute trend
REM turns down while still in profit. Operator's choice 2026-10-22, logged in FREEZE.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_peak_watch.log
echo ===== PEAK WATCH %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\peak_watch.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
