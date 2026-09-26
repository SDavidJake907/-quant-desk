@echo off
REM Point-in-time news archive — the one thing the weekend can gather.
REM
REM The scanner already runs seven days a week and on a weekend it gathers
REM nothing: no bars print, so Saturday re-reads Friday's series, finds the
REM same setups, ages them all past the freshness window and produces zero
REM candidates. Measured across 28 weekend scans: zero candidates, every time.
REM
REM News does not stop. Earnings pre-announcements, merger agreements and
REM guidance land disproportionately on Friday night and over the weekend,
REM and until now all of it was discarded — news_store is only reached from
REM inside "if news_gate and candidates", and on a weekend there are never any
REM candidates.
REM
REM WHY DAILY AND NOT WEEKENDS ONLY
REM The scan stores news only for the handful of names that became candidates,
REM which on most days is none. So every day loses the same thing a weekend
REM does, and there is no reason to archive Saturday but not Tuesday.
REM
REM RUNS AFTER THE OTHER OFF-HOURS JOBS
REM 14:00 AK = 18:00 ET, after DATA at 13:00 and OUTCOMES at 13:30, so three
REM jobs are not competing for the same provider at once.
REM
REM SUPERVISED, BUT UNLIKE DATA A MISSED DAY IS NOT PERMANENT.
REM Yahoo's RSS feed carries recent items, so a failed run loses at most the
REM stories that fall off the feed before the next one. Worth retrying, not
REM worth retrying hard — the same slow backoff as the bar collector, for the
REM same reason: the likeliest failure is rate limiting, and hammering a
REM throttling provider makes the next attempt worse.
REM
REM It places no order, reads no account and touches no policy. It appends to
REM data/news and nothing in the trading path reads it.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled.log
echo. >> "%LOG%"
echo ===== NEWS %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\watchdog.py" --max-restarts 3 --backoff 60 --max-backoff 600 -- ".venv\Scripts\python.exe" "scripts\collect_news.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
