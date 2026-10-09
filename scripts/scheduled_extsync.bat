@echo off
REM Extended-hours picks sync (WORKLIST §59): hourly 12:35-04:35 AK -- after-hours,
REM overnight (Sunday 16:00 AK on), premarket. Outside a session it prints
REM "market closed" and sends nothing. Regular hours stay with scheduled_scan.bat.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_extsync.log
echo. >> "%LOG%"
echo ===== EXTSYNC %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\picks_sync.py" --submit >> "%LOG%" 2>&1
exit /b %errorlevel%
