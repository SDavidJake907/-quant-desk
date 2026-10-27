@echo off
REM Morning leaders, weekdays 05:00 AK (stock-scanner-leaders): the momentum
REM top 20 and the 4 still rising, to logs\leaders\. REPORT ONLY -- it never
REM edits picks.txt or sends an order (operator's choice 2026-10-18). Before
REM the 05:30 open and Gemma's 05:45. Own log (WORKLIST §40).

cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_leaders.log
echo. >> "%LOG%"
echo ===== LEADERS %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\morning_leaders.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
