@echo off
rem Rebuilds logs\dashboard\command_center.html and opens it (read-only; sends no orders).
cd /d "%~dp0.."
".venv\Scripts\python.exe" "scripts\command_center.py" --open >> logs\scheduled_command_center.log 2>&1
