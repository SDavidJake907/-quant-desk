@echo off
REM Weekend pause of the picks book (scripts\picks_pause.py): %1 is --pause or --resume.
REM Run by one-time tasks; own log (WORKLIST §40).

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_picks_pause.log
echo. >> "%LOG%"
echo ===== PICKS-PAUSE %1 %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\picks_pause.py" %1 >> "%LOG%" 2>&1
exit /b %errorlevel%
