@echo off
REM Gemma's long-term memory, LIVE (advisor/hindsight_memory.py; operator 2026-10-19).
REM 05:05 AK weekdays and 16:30 Sunday, before her runs: the audit log -> feed (trading venv),
REM then Hindsight retains new memories and writes her lessons (the separate hindsight-sandbox venv).
REM A failure here only means no lessons block: her 05:45 run goes on without it. Own log (WORKLIST 40).

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_hindsight.log
echo. >> "%LOG%"
echo ===== HINDSIGHT %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\hindsight_feed.py" >> "%LOG%" 2>&1
if errorlevel 1 exit /b 1
"C:\Users\kitti\hindsight-sandbox\Scripts\python.exe" "scripts\hindsight_lessons.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
