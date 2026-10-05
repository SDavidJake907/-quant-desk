@echo off
REM Friday close-out. Runs before the bell so the book is flat over the weekend.
REM
REM TWO TRIGGERS, ONE JOB
REM US markets close at 13:00 ET on a few days a year. A task pinned to 15:45
REM ET fires nearly three hours AFTER the bell on those days and can flatten
REM nothing. So both an early and a normal trigger run this file, each asks
REM Alpaca's calendar whether the bell is within 25 minutes, and only the one
REM in the window acts. The other exits 0 having done nothing.
REM
REM WHY 11:45 AK
REM 11:45 Alaska is 15:45 Eastern, fifteen minutes before the close. Late
REM enough that the day has been given its chance, early enough that a market
REM order fills and a failure still leaves time to close by hand. The last scan
REM of the day runs at 12:03, after the close, and only evaluates.
REM
REM NOT SUPERVISED BY watchdog.py, WHICH REFUSES ORDER SUBMISSION OUTRIGHT.
REM A process that may have reached the broker and died before reading the
REM response has unknown position state; restarting it could sell twice. This
REM script is instead safe to re-run BY HAND: an already-cancelled order and an
REM already-closed position are both treated as success, because the desired
REM state is what matters.
REM
REM The exit code is the contract. Non-zero means something is still open, and
REM Task Scheduler will show it red rather than leaving a position exposed.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled.log
echo. >> "%LOG%"
echo ===== FLATTEN %DATE% %TIME% ===== >> "%LOG%"

REM --only-if-closing-within IS LOAD-BEARING. Without it BOTH triggers flatten
REM unconditionally, and the 08:45 AK one is 12:45 ET — the middle of the
REM session. The book would be closed three hours early every Friday.
".venv\Scripts\python.exe" "scripts\flatten.py" --submit --only-if-closing-within 25 --only-before-closure >> "%LOG%" 2>&1
exit /b %errorlevel%
