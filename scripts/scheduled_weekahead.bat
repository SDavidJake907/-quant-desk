@echo off
REM The daily brief (WORKLIST §52): calendar, news, the President, and which
REM stocks each item touches. 03:00 AK daily -- before TradingAgents takes the
REM GPU at 03:30 -- so Gemma reads it at 05:45. Writes data\brief\ and
REM logs\week_ahead\; places no order and touches no policy.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

REM One log per task (WORKLIST §40).
set LOG=logs\scheduled_weekahead.log
echo. >> "%LOG%"
echo ===== WEEKAHEAD %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\week_ahead.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
