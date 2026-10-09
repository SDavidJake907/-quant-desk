@echo off
REM Weekend pause of the picks book (scripts\picks_pause.py): %* is --pause [--keep-best N] or --resume.
REM Run by one-time tasks; own log (WORKLIST §40).

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_picks_pause.log
echo. >> "%LOG%"
echo ===== PICKS-PAUSE %* %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\picks_pause.py" %* >> "%LOG%" 2>&1
exit /b %errorlevel%
