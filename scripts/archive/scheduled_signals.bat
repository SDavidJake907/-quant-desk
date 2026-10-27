@echo off
REM Pine signal paper books (scripts/signal_live.py, TRIALS #80): every 5 minutes,
REM around the clock (24/5 -- regular books 9:30-16:00 ET, 24h books every session). VIRTUAL money -- never sends an order.
REM Friday 20:00 to Sunday 20:00 ET it only rewrites the leaderboard.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_signals.log
echo ===== SIGNALS %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\signal_live.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
