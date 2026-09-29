@echo off
REM Point-in-time news and auction prints. 19:00 AK = 23:00 ET, after the
REM extended-hours collection at 18:00 so two jobs are not racing the provider.
REM
REM WHY THESE TWO AND NOT ALL FOUR
REM Alpaca serves four datasets this project was not collecting: news (back to
REM 2015), auctions (2020), trades (2020) and quotes (2020). News and auctions
REM are small -- tens of items per symbol-year, two prints per symbol-day.
REM Trades and quotes are hundreds of thousands and millions per symbol-DAY,
REM so they are fetched per-setup when measuring fills or spread rather than
REM hoarded.
REM
REM NEWS IS THE ONE THAT CHANGES WHAT IS POSSIBLE. ranking.py has never been
REM scored because data/news held two days: asking the model about a 2025 setup
REM gave it no news or TODAY's news, and the second is lookahead. With dated
REM history every past candidate can be given the news that existed then.
REM
REM Written to data/news_history, SEPARATE from data/news. One is history; the
REM other is a record of what the live scan actually saw at decision time.
REM Conflating them would let hindsight into the audit trail.
REM
REM Unsupervised and its exit code ignored: nothing downstream depends on it
REM and a failed night costs research data, not a trade.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled.log
echo. >> "%LOG%"
echo ===== EXTRAS %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\collect_alpaca_extras.py" --index sp500 --news --auctions --start 2024-09-01 >> "%LOG%" 2>&1
exit /b 0
