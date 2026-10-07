@echo off
REM Gemma's book: the junior trader's morning decision, 05:45 AK weekdays. Names only; the system prices, sizes and stops.
REM WORKLIST §42. Own log (WORKLIST §40): never share a log with another task.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_gemma.log
echo. >> "%LOG%"
echo ===== GEMMA %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\gemma_trade.py" --submit >> "%LOG%" 2>&1
exit /b %errorlevel%
