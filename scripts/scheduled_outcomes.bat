@echo off
REM Append what actually happened to each closed trade.
REM
REM READ-MOSTLY AND OUTSIDE THE FROZEN TRADING PATH. It reads the broker and
REM appends trade_outcome records. It places no order, cancels nothing and
REM touches no policy, so it cannot influence a trading decision and running it
REM does not end an evaluation window.
REM
REM 13:30 AK, half an hour after the data collector, an hour and a half after
REM the close. Late enough that the day's fills have settled at the broker.
REM
REM Idempotent by design: outcomes already in the log are skipped, so a missed
REM day is picked up by the next run and a double run writes nothing twice.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

REM One log per task (WORKLIST §40): a shared log locked out any task that
REM started while another was writing, and that task silently did not run.
set LOG=logs\scheduled_outcomes.log
echo. >> "%LOG%"
echo ===== OUTCOMES %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\record_outcomes.py" --write >> "%LOG%" 2>&1

REM One scorecard split by strategy: scanner vs manual picks vs momentum
REM (WORKLIST §37). Read-only.
".venv\Scripts\python.exe" "scripts\strategy_scorecard.py" >> "%LOG%" 2>&1

REM Second opinions graded: TradingAgents and the Gemma read against the S&P
REM over 21 sessions (WORKLIST §38). Advisory; read-only.
".venv\Scripts\python.exe" "scripts\second_opinion.py" --grades >> "%LOG%" 2>&1

REM The competition (WORKLIST §46): snapshot both books, draw/advance the
REM month's ghost and benchmark paper books, then the standings table.
".venv\Scripts\python.exe" "scripts\competition.py" snapshot >> "%LOG%" 2>&1
".venv\Scripts\python.exe" "scripts\competition.py" standings >> "%LOG%" 2>&1

REM Mark the model's recorded calls against outcomes, the day they land.
REM Read-only, advisory; its exit code is ignored like the archive's.
".venv\Scripts\python.exe" "scripts\score_gemma.py" >> "%LOG%" 2>&1

REM --- archive what the broker holds -------------------------------
REM The audit log records what was DECIDED; the broker records what
REM FILLED, and only the first is in git. reconcile.py already names the
REM exposure: realized R is recomputed from order history the broker
REM prunes. Twelve closed trades is the only non-backtest evidence here.
REM
REM Routine pruning, an account reset, a key rotation or a CLEARING
REM MIGRATION would take it -- and a clearing change is the class of
REM event no identifier in FREEZE captures, the same blind spot as the
REM 23x5 market change on 2026-12-06.
REM
REM Dated snapshots, never overwritten, exit code ignored.
".venv\Scripts\python.exe" "scripts\archive_broker.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
