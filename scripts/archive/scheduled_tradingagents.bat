@echo off
REM TradingAgents second opinion, recorded only. Usage: scheduled_tradingagents.bat premarket|live
REM
REM Rates held positions and the latest candidates with TradingAgents on the
REM one local model, and appends each rating to the audit log. It sends no
REM order and gates nothing; score_gemma.py marks the ratings against outcomes.
REM
REM IN PIECES: TradingAgents checkpoints each finished agent step. A pass that
REM reaches the next scan's deadline (:02/:32) is stopped and the next pass
REM resumes the same name, so a long run fits in the gaps between scans.
REM
REM UNSUPERVISED ON PURPOSE: a missed pass is retried by the next one, and the
REM checkpoint means nothing finished is lost.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

REM One log per task (WORKLIST §40): a shared log locked out any task that
REM started while another was writing, and that task silently did not run.
set LOG=logs\scheduled_tradingagents.log
echo. >> "%LOG%"
echo ===== TRADINGAGENTS %1 %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\tradingagents_pass.py" --mode %1 --min-minutes 3 >> "%LOG%" 2>&1
exit /b %errorlevel%
