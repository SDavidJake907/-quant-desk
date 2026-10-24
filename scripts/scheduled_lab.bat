@echo off
REM Gemma's research lab (scripts/gemma_lab.py, scanner/lab/): Saturdays, markets closed.
REM 15 recipes on 2007-2021, then at most one nomination for the hidden years (2022-23, then
REM 2024-today). Trades nothing. Operator 2026-10-23: "let her run backtests".
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_lab.log
echo ===== LAB %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\gemma_lab.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
