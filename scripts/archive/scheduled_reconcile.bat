@echo off
REM Reconciliation verification: audit log intents vs broker state.
REM
REM READ-MOSTLY AND OUTSIDE THE FROZEN TRADING PATH. It reads the broker and
REM the audit log, compares them, and reports mismatches. It places no order,
REM cancels nothing, and touches no policy, so it cannot influence a trading
REM decision and running it does not end an evaluation window.
REM
REM Run after outcomes (13:30 AK) so the day's fills have settled at the broker.
REM Idempotent: mismatch events already in the log are not duplicated.
REM
REM With --write, mismatches are appended to the audit log as reconcile_mismatch
REM records. Without it, this is a read-only report.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_reconcile.log
echo. >> "%LOG%"
echo ===== RECONCILE %DATE% %TIME% ===== >> "%LOG%"

REM Read-only check; exit non-zero on mismatch so Task Scheduler shows red.
".venv\Scripts\python.exe" "scripts\reconcile_check.py" >> "%LOG%" 2>&1
set /p EXITCODE=<nul
if %errorlevel% neq 0 exit /b %errorlevel%

exit /b 0
