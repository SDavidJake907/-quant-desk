"""TradingAgents on this month's momentum picks, and the monthly report. Never trades.

    python scripts/momentum_report.py              # rate the latest dry-run picks, write the report
    python scripts/momentum_report.py --dry-run    # show what would be rated
    python scripts/momentum_report.py --force      # allow a run during market hours

Reads the newest record in data/momentum/dry_run.jsonl (momentum_rebalance.py),
runs TradingAgents on each pick through tradingagents_pass's runner (same
worker, same scan-deadline protection), and writes

    data/momentum/reports/YYYY-MM-DD/report.md     the report
    data/momentum/reports/YYYY-MM-DD/<SYM>.json    TradingAgents' full reasoning
    data/momentum/ratings.jsonl                    one row per rated pick (TRIALS.md #44)

These live under data/momentum, NOT logs/tradingagents, so the advisor's
30-day cleanup never deletes them: the #44 test needs the reasoning a year on.
Each rating is also written to the audit log with mode=momentum.

RECORD-ONLY. Nothing here changes the book. ~2 min of GPU per pick, so it
refuses to start during market hours (the scanner owns the model) unless
--force, and the runner still stops at a scan deadline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, time as dtime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

from scanner.advisor.tradingagents import parse_worker_output, rating_event, worker_deadline
from scanner.config import settings
from scanner.execution.audit import append_event
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id
from scanner.features.intraday import EXCHANGE_TZ
from scanner.momentum import report
from scanner.sources import composite
from scanner.sources.index_history import SP400, SP500

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tradingagents_pass import TICKER_TIMEOUT_S, _run_one  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / "data" / "momentum" / "dry_run.jsonl"
REPORTS = ROOT / "data" / "momentum" / "reports"
RATINGS = ROOT / "data" / "momentum" / "ratings.jsonl"


def market_hours(now: datetime) -> bool:
    et = now.astimezone(EXCHANGE_TZ)
    return et.weekday() < 5 and dtime(9, 20) <= et.time() <= dtime(16, 15)


def latest_record() -> dict | None:
    if not RECORD.exists():
        return None
    lines = [l for l in RECORD.read_text(encoding="utf-8").splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else None


def main(dry: bool, force: bool) -> int:
    record = latest_record()
    if record is None:
        print("no dry-run record yet: run scripts/momentum_rebalance.py first")
        return 1
    now = datetime.now().astimezone()
    picks = record["picks"]
    print(f"momentum report for the {record['date']} rebalance: {', '.join(picks)}")
    if dry:
        return 0
    if market_hours(now) and not force:
        print("market hours: the scanner owns the model. Run after the close, or --force.")
        return 1
    if not Path(settings.tradingagents_python).exists():
        print(f"TradingAgents venv not found: {settings.tradingagents_python}")
        return 1
    folder = REPORTS / record["date"]
    folder.mkdir(parents=True, exist_ok=True)
    policy, ratings = TradingPolicy().fingerprint, {}
    # The record carries the EXCHANGE date (New York); TradingAgents rejects a
    # date after this machine's local one ("cannot be in the future") -- which
    # in Alaska is every evening after 20:00. Both see data only through the
    # last completed session, so the earlier of the two is the honest date.
    as_of = min(record["date"], now.date().isoformat())
    for sym in picks:
        request = {"ticker": sym, "date": as_of, "model": settings.triage_model,
                   "deadline": worker_deadline(datetime.now().astimezone(),
                                               timeout_s=TICKER_TIMEOUT_S).isoformat(),
                   "report_dir": folder.as_posix(), "portfolio": None}
        print(f"  {sym} ...", flush=True)
        output, ended = _run_one(request, folder / f"{sym}.log")
        result = parse_worker_output(output) if ended == "done" else None
        if result is None:
            print(f"  {sym}: {ended}, not rated")
            continue
        append_event(settings.audit_log_path, rating_event(
            result, mode="momentum", at=datetime.now().astimezone(), policy=policy, build=build_id()))
        decision = json.loads((folder / f"{sym}.json").read_text(encoding="utf-8")).get("final_trade_decision", "")
        ratings[sym] = {"rating": result.rating, "trader": result.trader or "-",
                        "summary": report.excerpt(str(decision))}
        print(f"  {sym}: {result.rating}  trader={result.trader or '-'}  {result.seconds:.0f}s")

    sector = {m.symbol: m.sector for m in asyncio.run(_universe())}
    (folder / "report.md").write_text(report.render(record, ratings, sector), encoding="utf-8")
    with RATINGS.open("a", encoding="utf-8") as handle:
        for row in report.rating_rows(record, ratings):
            handle.write(json.dumps(row) + "\n")
    print(f"\n{len(ratings)}/{len(picks)} rated. Report: {folder / 'report.md'}")
    return 0


async def _universe():
    async with httpx.AsyncClient(timeout=60) as client:
        return await composite.load_universe(client, (SP500, SP400))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    a = p.parse_args()
    raise SystemExit(main(a.dry_run, a.force))
