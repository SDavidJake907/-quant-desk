@echo off
REM Scheduled market-hours scan. Wrapped rather than called directly because
REM config paths (logs/, data/) are relative to the project root, and a task
REM started from C:\Windows\System32 would write its audit log somewhere nobody
REM ever looks — the classic "works by hand, silently wrong on a schedule" bug.
REM
REM trade.py refuses to run when the market is closed, so weekends and holidays
REM cost one process start and nothing else.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled.log
echo. >> "%LOG%"
echo ===== %DATE% %TIME% ===== >> "%LOG%"

REM RE-ARM ANYTHING NAKED BEFORE DOING ANYTHING ELSE.
REM
REM The scan already detects positions with no live stop and shouts about
REM them — and then does nothing but print "re-arm with: protect.py --submit".
REM That is a manual step in an unattended system, and the last time it
REM mattered a power outage meant nobody was watching: three positions worth
REM 58,701 dollars sat with no floor under them through a weekend and all of
REM Monday.
REM
REM protect.py is safe to run on a schedule. It reads the ORIGINAL stop and
REM target back out of the audit log rather than re-deriving them from
REM today's price, and it refuses to re-arm a position whose stop has already
REM been breached, reporting those for a human instead.
REM
REM No market-hours guard, deliberately. A protective order sent after the
REM bell is QUEUED for the next open rather than rejected, and a stop that
REM activates at the open beats a position that is naked into it.
".venv\Scripts\python.exe" "scripts\protect.py" --submit >> "%LOG%" 2>&1

REM THE HOLD LIMIT RUNS NEXT, THEN THE SCAN.
REM
REM max_hold_bars=60 is how +0.218R was measured — 5.6% of holdout trades
REM exited on it rather than at a stop or a target. It was declared in
REM TradingPolicy and enforced NOWHERE: not one live trade has ever exited on
REM it, and every recorded outcome is a stop, a target or a weekend flatten.
REM
REM Before the scan rather than after, because the backtest frees the slot at
REM the same bar it exits. Closing first lets this cycle reuse the slot, which
REM is what was measured.
REM
REM It refuses to act when the market is shut, so the 16:03 ET run reports and
REM closes nothing. NOT watchdog-wrapped: it submits closing orders, and a
REM process that may have reached the broker must be reconciled rather than
REM blindly restarted.
".venv\Scripts\python.exe" "scripts\manage_positions.py" --execute >> "%LOG%" 2>&1

REM --- SWEEP-SCANNER ENTRIES PAUSED, 2026-10-06 (WORKLIST §37) ---------
REM
REM trade.py no longer runs on the schedule. Measured: -0.041R per trade on
REM the unseen 2018-24 store (§30) and -11.1% in the two-year account replay
REM with live settings (§29). The code is kept; run it by hand to look:
REM     .venv\Scripts\python.exe scripts\trade.py          (preview, no orders)
REM
REM The Gemma position review that ran here is removed too: 119 calls, 119
REM "hold", including five trades that went on to stop out (§27). A call
REM that never varies carries no information and cost the GPU every 30 min.
REM
REM What stays on this half-hourly rhythm is SAFETY: protect.py above keeps
REM a stop under every manual and scanner position; manage_positions.py
REM applies the 60-bar limit to scanner positions only.
exit /b 0
