@echo off
REM Monday research, 04:30 AK (WORKLIST §48): refresh Gemma's rolling list
REM (committed on its own) and write the operator's research pack to
REM logs\research\. Before Gemma's 05:45 decision. Own log (WORKLIST §40).

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_monday.log
echo. >> "%LOG%"
echo ===== MONDAY %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\monday_research.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
