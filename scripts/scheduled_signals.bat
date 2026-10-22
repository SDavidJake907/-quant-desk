@echo off
REM Pine signal paper books (scripts/signal_live.py, TRIALS #80): every 5 minutes
REM 05:30-12:10 AK (regular hours). VIRTUAL money -- never sends an order.
REM Outside 9:30-16:00 ET it only rewrites the day's leaderboard.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_signals.log
echo ===== SIGNALS %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\signal_live.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
