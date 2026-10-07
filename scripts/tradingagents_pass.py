"""Record TradingAgents' rating of held positions and candidates. Never trades.

    python scripts/tradingagents_pass.py --mode premarket
    python scripts/tradingagents_pass.py --mode live
    python scripts/tradingagents_pass.py --tickers SNPS AON --dry-run

READ-ONLY AGAINST THE BROKER: positions are read so the agents see
the real book; no order is sent, cancelled or changed. Each rating is appended
to the audit log as `tradingagents_rating` (see scanner.advisor.tradingagents).

During scan hours a run starts only with --min-minutes to spare before the
next scan and is stopped at the deadline, so the shared model is free when the
news gate asks. A stopped run records nothing yet: TradingAgents checkpoints
every finished agent step, so the next pass resumes the same name where it
stopped. That is what splits one long run into pieces that fit between scans.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

from scanner.advisor.tradingagents import (
    error_event, expired_folders, may_start, outcome, parse_worker_output,
    pick_tickers, rated_on, rating_event, scan_deadline, stale_checkpoints,
    worker_deadline,
)
from scanner.advisor import opinions, scan_watch
from scanner.config import settings
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.audit import append_event
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id
from scanner.llm.memory import _rows

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "scripts" / "ta_worker.py"
REPORTS = ROOT / "logs" / "tradingagents"

#: No single ticker may hold the GPU longer than this, even off-hours.
TICKER_TIMEOUT_S = 45 * 60

#: Days of reports kept, ours and TradingAgents'. The rating itself lives in
#: the audit log for good; a report is only the reasoning behind it.
REPORT_DAYS = 30

TA_HOME = Path.home() / ".tradingagents"
_STATE_LOG = re.compile(r"^full_states_log_(\d{4}-\d{2}-\d{2})\.json$")


def _sweep_orphans() -> None:
    """Kill any ta_worker still running from an earlier pass.

    The worker exits by itself at its deadline; this is the second line, for a
    worker that somehow outlived it. Two runs on one GPU is the crash this
    whole layer is built to avoid.
    """
    command = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
               "Where-Object { $_.CommandLine -like '*ta_worker.py*' } | "
               "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                         capture_output=True, text=True).stdout.split()
    if out:
        print(f"  ! killed leftover worker(s): {', '.join(out)}")


def _cleanup(now: datetime) -> None:
    """Delete what can never be used again. Never touches the audit log."""
    removed = 0
    cp_dir = TA_HOME / "cache" / "checkpoints"
    if cp_dir.is_dir():
        files = {f.name: datetime.fromtimestamp(f.stat().st_mtime).astimezone()
                 for f in cp_dir.iterdir() if f.is_file()}
        for name in stale_checkpoints(files, now):
            try:
                (cp_dir / name).unlink()
                removed += 1
            except OSError as exc:      # locked by a live run: next pass
                print(f"  ! checkpoint {name} kept: {exc}")
    if REPORTS.is_dir():
        for name in expired_folders([d.name for d in REPORTS.iterdir() if d.is_dir()],
                                    now.date(), keep_days=REPORT_DAYS):
            shutil.rmtree(REPORTS / name, ignore_errors=True)
            removed += 1
    for log in (TA_HOME / "logs").glob("*/TradingAgentsStrategy_logs/full_states_log_*.json"):
        match = _STATE_LOG.match(log.name)
        if match and expired_folders([match.group(1)], now.date(), keep_days=REPORT_DAYS):
            log.unlink(missing_ok=True)
            removed += 1
    if removed:
        print(f"  cleaned {removed} stale checkpoint(s)/report(s)")


async def _scan_watch() -> tuple[str, ...]:
    async with httpx.AsyncClient(timeout=60) as client:
        return await scan_watch.build(client)


async def _book() -> dict | None:
    """Positions as TradingAgents' PortfolioContext expects them, or None.

    Cash is left out on purpose. The book's fingerprint keys the run's
    checkpoint, and cash moves on every fill and accrual -- including it would
    restart a half-finished run from scratch at each pass.
    """
    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        try:
            positions = await broker._get("/v2/positions")
        except BrokerError as exc:
            print(f"  ! broker unreadable, running without a book: {exc}")
            return None
    return {"currency": "USD",
            "positions": [{"ticker": p["symbol"], "quantity": float(p["qty"]),
                           "average_price": float(p["avg_entry_price"])}
                          for p in positions or []]}


def _scan_running() -> bool:
    out = subprocess.run(["schtasks", "/query", "/tn", "stock-scanner-scan", "/fo", "csv", "/nh"],
                         capture_output=True, text=True).stdout
    return "Running" in out


def _run_one(request: dict, log: Path) -> tuple[str, str]:
    """Run the worker to completion or deadline. Returns (output, outcome).

    Output goes to a file, never a pipe: a pipe nobody reads until exit fills
    at ~64 KB and blocks the worker mid-run, which would then sit until its
    deadline and record nothing. The file is kept beside the report.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    killed_for = ""
    with open(log, "w", encoding="utf-8", errors="replace") as sink:
        proc = subprocess.Popen([settings.tradingagents_python, str(WORKER)], cwd=ROOT,
                                stdin=subprocess.PIPE, stdout=sink,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        proc.stdin.write(json.dumps(request))
        proc.stdin.close()
        started = time.monotonic()
        while proc.poll() is None:
            deadline = scan_deadline(datetime.now().astimezone())
            if deadline is not None and datetime.now().astimezone() >= deadline:
                killed_for = "scan"
            elif time.monotonic() - started > TICKER_TIMEOUT_S:
                killed_for = "timeout"
            if killed_for:
                proc.kill()
                proc.wait()
                break
            time.sleep(5)
    output = log.read_text(encoding="utf-8", errors="replace")
    return output, outcome(returncode=proc.returncode, killed_for=killed_for)


def main(mode: str, tickers: list[str], limit: int | None, min_minutes: float, dry: bool) -> int:
    # 50 premarket: held + momentum + core + Gemma's list, ~2 min each, inside
    # the 03:30-05:32 window (the pass stops itself before the first scan).
    limit = limit or (50 if mode == "premarket" else 8)
    if not Path(settings.tradingagents_python).exists():
        print(f"TradingAgents venv not found: {settings.tradingagents_python}")
        return 1
    now = datetime.now().astimezone()
    if not dry:
        _sweep_orphans()
        _cleanup(now)
    rows = _rows(settings.audit_log_path)
    book = asyncio.run(_book())
    held = tuple(p["ticker"] for p in (book or {}).get("positions", []))
    # SECOND OPINIONS (WORKLIST §38, 2026-10-06): rate what the operator and
    # the momentum book actually hold or watch -- held positions, then the
    # momentum picks, then watchlists/core.txt. The sweep scanner is paused,
    # so its setups and slate are no longer rated. Every rating is graded by
    # second_opinion.py; none of them gates anything.
    watch: tuple[str, ...] = ()
    if not tickers:
        _, picks, _ = opinions.momentum_picks()
        watch = (*picks, *scan_watch.operator_names(Path("watchlists/core.txt")),
                 *scan_watch.operator_names(Path("watchlists/gemma.txt")))
    names = tuple(t.upper() for t in tickers) or pick_tickers(
        held=held, rows=[], skip=rated_on(rows, now.date()), limit=limit, watch=watch)
    print(f"TRADINGAGENTS {mode}  {now:%Y-%m-%d %H:%M}  model {settings.triage_model}")
    print(f"  held {list(held) or 'none'}  momentum + core {list(watch) or 'none'}")
    print(f"  ->  rating {list(names) or 'nothing'}")
    if dry or not names:
        return 0

    policy = TradingPolicy().fingerprint
    for ticker in names:
        if not may_start(datetime.now().astimezone(), min_minutes=min_minutes):
            print(f"  next scan too close; {ticker} and later names wait for the next pass")
            break
        while _scan_running() and may_start(datetime.now().astimezone(),
                                            min_minutes=min_minutes):
            time.sleep(15)      # the scan owns the model; wait it out
        if _scan_running():
            print(f"  scan still running; {ticker} and later names wait for the next pass")
            break
        deadline = worker_deadline(datetime.now().astimezone(), timeout_s=TICKER_TIMEOUT_S)
        request = {"ticker": ticker, "date": f"{now:%Y-%m-%d}", "model": settings.triage_model,
                   "deadline": deadline.isoformat(),
                   "report_dir": (REPORTS / f"{now:%Y-%m-%d}").as_posix(), "portfolio": book}
        print(f"  {ticker} ...", flush=True)
        log = REPORTS / f"{now:%Y-%m-%d}" / f"{ticker}.log"
        output, ended = _run_one(request, log)
        result = parse_worker_output(output) if ended == "done" else None
        if ended == "paused":
            print(f"  {ticker}: paused at the deadline; resumes next pass")
            continue
        if result is None:
            tail = " | ".join(output.strip().splitlines()[-3:])[-300:]
            append_event(settings.audit_log_path, error_event(
                ticker, f"{ended}: {tail}", mode=mode, at=datetime.now().astimezone(),
                policy=policy, build=build_id()))
            print(f"  {ticker}: FAILED, skipped for today ({log}) {tail}")
            continue
        append_event(settings.audit_log_path, rating_event(
            result, mode=mode, at=datetime.now().astimezone(), policy=policy,
            build=build_id()))
        print(f"  {ticker}: {result.rating}  trader={result.trader or '-'}  "
              f"{result.seconds:.0f}s  {result.report}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["premarket", "live", "manual"], default="manual")
    p.add_argument("--tickers", nargs="*", default=[], help="rate these instead")
    p.add_argument("--limit", type=int, default=None,
                   help="names per pass (default: 30 premarket -- ~110 minutes of GPU "
                        "before the first scan at ~2 min a name -- else 8)")
    p.add_argument("--min-minutes", type=float, default=15.0,
                   help="during scan hours, the least time before a scan to start a name")
    p.add_argument("--dry-run", action="store_true", help="show the names, run nothing")
    a = p.parse_args()
    raise SystemExit(main(a.mode, a.tickers, a.limit, a.min_minutes, a.dry_run))
