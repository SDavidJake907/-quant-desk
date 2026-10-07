@echo off
REM Momentum paper rebalance, weekdays 11:40 AK (15:40 ET). Acts once a month;
REM see register_tasks.ps1 and WORKLIST §37. NOT watchdog-wrapped: it submits
REM orders, and a process that may have reached the broker must be read back,
REM not blindly restarted -- the fixed mom- client ids make tomorrow's retry safe.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

REM One log per task (WORKLIST §40): a shared log locked out any task that
REM started while another was writing, and that task silently did not run.
set LOG=logs\scheduled_momentum.log
echo. >> "%LOG%"
echo ===== MOMENTUM %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\momentum_rebalance.py" --paper --submit >> "%LOG%" 2>&1
exit /b %errorlevel%
