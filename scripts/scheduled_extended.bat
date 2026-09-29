@echo off
REM Extended-hours SIP bars, 04:00-19:30 ET, into a SEPARATE store.
REM
REM WHY COLLECT WHAT THE SYSTEM CANNOT TRADE ON
REM Bars that are not saved cannot be recovered. Alpaca serves them, they cost
REM nothing, and the questions they answer are ones the regular store is
REM structurally unable to reach: what the pre-market range does to the open,
REM whether an overnight gap was already visible at 08:00, whether the sweep
REM the screen finds at 09:30 was really set up two hours earlier.
REM
REM MEASURED, so the expectations are right: across AAPL, NVDA and TSLA in
REM September 2026, regular hours carried 82.3% of dollar volume. The whole
REM "London session" (04:00-08:00 ET) was 1.4% and the Asia window 0.3%. The
REM Asia/London/New York model is a forex and futures framework; in US equities
REM those sessions barely exist. What DOES exist is a thin pre-market range
REM followed by 16.6% of the day's volume in the 09:00 hour.
REM
REM IT WRITES TO data/intraday_extended AND THE TRADING PATH NEVER READS IT.
REM A 32-bucket session is a different instrument from a 13-bucket one, and
REM every structural rule here -- crosses_session, session_slot, the flatten,
REM the 60-bar hold -- assumes the second. Mixing them would silently
REM invalidate every measurement taken on the regular store.
REM
REM 18:00 AK = 22:00 ET, after the post-market session has closed, so the day
REM is complete when it runs. Unsupervised and its exit code ignored: nothing
REM downstream depends on it, and a failed night costs one day of research
REM data rather than a trade.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled.log
echo. >> "%LOG%"
echo ===== EXTENDED %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\collect_alpaca.py" --index sp500 --start 2026-06-01 --extended >> "%LOG%" 2>&1
exit /b 0
