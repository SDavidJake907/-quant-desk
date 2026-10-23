@echo off
REM Opening-range breakout day book (scripts/orb_trade.py, execution/orb.py): every 5 min,
REM 05:46-11:56 AK (9:46-15:56 ET) weekdays. Builds the day's shortlist from Gemma's saved
REM pool at 9:45 ET, buys breaks until close-30, sells everything at close-10.
REM Operator 2026-10-23 ("lets tie this in"); 10% taken from Gemma's share with her agreement.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_orb.log
echo ===== ORB %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\orb_trade.py" --submit >> "%LOG%" 2>&1
exit /b %errorlevel%
