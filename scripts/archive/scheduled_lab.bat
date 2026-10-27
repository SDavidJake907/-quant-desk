@echo off
REM Gemma's research lab, ALL WEEKEND (scripts/gemma_lab.py --weekend, scanner/lab/): a session every
REM 2 hours rotating big / mid / small caps until Sunday 14:00 AK; one hidden-years nomination per group
REM per weekend. Trades nothing. Operator 2026-10-23: "lets run this on a schedule. all weekend".
REM Triggers Fri 17:30, Sat 06:00, Sun 06:00 AK with IgnoreNew: the later ones only restart a run
REM that died (a reboot); a running weekend is left alone.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_lab.log
echo ===== LAB WEEKEND %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\gemma_lab.py" --weekend >> "%LOG%" 2>&1
exit /b %errorlevel%
