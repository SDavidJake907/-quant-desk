@echo off
REM The desk officer's evening journal, 13:45 AK weekdays. Checks are code; Gemma writes them up.
REM WORKLIST §42. Own log (WORKLIST §40): never share a log with another task.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_journal.log
echo. >> "%LOG%"
echo ===== JOURNAL %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\desk_journal.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
