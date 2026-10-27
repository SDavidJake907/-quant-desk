@echo off
REM Median sector relative strength, written BEFORE the first scan.
REM
REM 05:00 AK, half an hour ahead of the 05:33 open scan. The timing is the
REM whole point and the first attempt got it wrong: this was put in
REM scheduled_data.bat at 13:00 AK, which stamps the file with THAT day.
REM Every scan the following morning asks for the following day, misses,
REM and recomputes -- so the job did nothing at all.
REM
REM Unsupervised and its exit code ignored on purpose. These medians are
REM advisory context for the ranking prompt and gate no trade, and
REM `sector_strength.today()` computes them itself when the file is absent
REM or carries another date. A failure here costs one scan 4.45s, not a
REM trade, so nothing should retry against a provider for it.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

REM One log per task (WORKLIST §40): a shared log locked out any task that
REM started while another was writing, and that task silently did not run.
set LOG=logs\scheduled_sector.log
echo. >> "%LOG%"
echo ===== SECTOR %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\refresh_sector_rs.py" --index sp500 >> "%LOG%" 2>&1
exit /b 0
