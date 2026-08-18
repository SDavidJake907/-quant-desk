@echo off
REM Off-hours data collection — this is what "ready and loaded with the correct
REM data" actually requires.
REM
REM Running the TRADER around the clock would not do it: trade.py exits within
REM seconds when the market is closed, and the bar cache is TTL-based, so an
REM idle process holds nothing warm. What keeps the system ready is history
REM that keeps growing while the market is shut.
REM
REM collect_intraday.py accumulates 30-minute bars past Yahoo's 60-day window.
REM That window is the hard ceiling on every backtest here, and it only lifts
REM by saving bars before they age out — which cannot be done retroactively.
REM
REM SUPERVISED, BECAUSE A MISSED DAY IS PERMANENT.
REM This job runs once a day, and bars it fails to save age out of Yahoo's
REM window for good. A transient network failure at 13:00 costs a day of
REM history no later run can recover — the one failure here worth retrying
REM automatically.
REM
REM Backoff is deliberately slow. The likeliest cause of a failed run is Yahoo
REM rate-limiting 1500 requests, and retrying hard against a provider that is
REM already throttling makes the next attempt worse. Three restarts at
REM 60s / 120s / 240s fit well inside the task's 2-hour limit.
REM
REM The TRADER is deliberately NOT supervised: watchdog.py refuses
REM --submit-paper outright. A process that may have reached the broker and
REM died before reading the response has unknown position state, and
REM restarting it can double a position. Its retry is the next scheduled scan
REM 30 minutes later, which re-reads the account before doing anything.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled.log
echo. >> "%LOG%"
echo ===== DATA %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\watchdog.py" --max-restarts 3 --backoff 60 --max-backoff 600 -- ".venv\Scripts\python.exe" "scripts\collect_intraday.py" --index all >> "%LOG%" 2>&1
exit /b %errorlevel%
