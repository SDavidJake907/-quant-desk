@echo off
REM Four-factor report (scripts/factor_report.py): Fridays 14:00 AK, after the 13:30 competition snapshot.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_factors.log
echo ===== FACTORS %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\factor_report.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
