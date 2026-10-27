@echo off
REM Gemma's morning portfolio review — read-only, advisory, never enforced.
REM
REM Writes data/brief/portfolio-review.json, which Gemma reads at 05:45 AK
REM as part of her facts block. It shows her open positions, by-book stats,
sector exposure, refusals, and TradingAgents ratings on her holdings.
REM
REM Places no order, cancels nothing, touches no policy. Safe to run on a
REM schedule during a frozen evaluation window.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_gemma_portfolio_review.log
echo. >> "%LOG%"
echo ===== GEMMA PORTFOLIO REVIEW %DATE% %TIME% ===== >> "%LOG%"

REM 03:00 AK daily, half an hour before Gemma's 05:30 decision.
REM She reviews the portfolio before choosing what to buy.
".venv\Scripts\python.exe" "scripts\gemma_portfolio_review.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
