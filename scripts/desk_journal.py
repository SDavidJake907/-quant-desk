"""The desk officer's evening journal (WORKLIST §42).

    python scripts/desk_journal.py          # today's journal, printed and saved
    python scripts/desk_journal.py --no-llm # findings only, no Gemma prose

Runs after the outcomes task (13:45 AK). The CHECKS are code
(scanner/ops/deskcheck.py): did every task run, did any log an error, does
any position lack a stop, how is money spread across books and sectors.
Gemma then writes the day up in plain words from those findings. Its prose
is printed BESIDE the findings, never instead of them, so a missed alarm
cannot be talked away.

Saved to logs/journal/YYYY-MM-DD.md and logged as `desk_journal`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution import bookorders, ownership
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.policy import TradingPolicy
from scanner.llm.ollama import LLMError, chat_json
from scanner.ops import deskcheck as dc
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500

POLICY = TradingPolicy()
LOGS = Path("logs")
JOURNAL = LOGS / "journal"
SCHEMA = {"type": "object", "properties": {"journal": {"type": "string"}},
          "required": ["journal"]}


def task_logs() -> dict[str, str]:
    out = {}
    for task in dc.EXPECTED_RUNS:
        path = LOGS / f"scheduled_{task}.log"
        out[task] = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    return out


async def gather(client) -> tuple[list[dc.Finding], dict, dict, bool]:
    today = date.today()
    findings: list[dc.Finding] = []
    broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
    market_day = True
    try:
        clock = await broker._get("/v2/calendar", start=today.isoformat(), end=today.isoformat())
        market_day = bool(clock)
    except BrokerError:
        pass
    logs = task_logs()
    if market_day:
        findings += dc.missing_runs(dc.runs_on(today, logs))
    findings += dc.trouble_lines(today, logs)

    positions = await broker._get("/v2/positions")
    owner_map = ownership.owners(await broker._get(
        "/v2/orders", status="all", limit=500, nested="true"))
    naked = [p.symbol for p in await broker.unprotected_positions()
             if ownership.owner(p.symbol, owner_map) not in POLICY.stopless_strategies]
    if naked:
        findings.append(dc.Finding(level="problem", text=f"no live stop on {', '.join(naked)}"))
    open_buys = [o["symbol"] for o in await broker._get("/v2/orders", status="open", nested="true")
                 if o.get("side") == "buy"]
    if open_buys:
        findings.append(dc.Finding(level="watch", text=f"buy orders still unfilled: {', '.join(open_buys)}"))

    universe = await composite.load_universe(client, COMPOSITE_1500)
    sector_of = {m.symbol: m.sector for m in universe}
    values = {p["symbol"]: float(p.get("market_value") or 0) for p in positions}
    shares, flags = dc.exposure(values, sector_of)
    findings += flags
    owner_of = {s: ownership.owner(s, owner_map) for s in values}
    return findings, shares, dc.books(positions, owner_of), market_day


def render(findings, shares, books, prose: str) -> str:
    today = date.today().isoformat()
    lines = [f"# Desk journal {today}", ""]
    problems = [f for f in findings if f.level == "problem"]
    lines.append(f"**{len(problems)} problem(s), "
                 f"{sum(f.level == 'watch' for f in findings)} to watch.**")
    lines += ["", "## Findings (checked by code)"]
    lines += [f"- [{f.level}] {f.text}" for f in findings] or ["- nothing wrong found"]
    lines += ["", "## Books"]
    for book, row in sorted(books.items()):
        lines.append(f"- {book}: {row['positions']} positions, ${row['value']:,.0f}, "
                     f"unrealized ${row['unrealized']:+,.0f}")
    if not books:
        lines.append("- no positions")
    if shares:
        lines += ["", "## Where the money is (all books)"]
        lines += [f"- {k}: {v:.0f}%" for k, v in shares.items()]
    lines += ["", "## Gemma's write-up (advisory)", "", prose or "_no write-up_", ""]
    return "\n".join(lines)


async def main(use_llm: bool) -> int:
    async with httpx.AsyncClient(timeout=120) as client:
        findings, shares, books, market_day = await gather(client)
        prose = ""
        if use_llm:
            facts = {"market_day": market_day,
                     "findings": [f.model_dump() for f in findings],
                     "books": books, "sector_share_pct": {k: round(v) for k, v in shares.items()}}
            ask = ("You are the desk officer of a small paper-trading desk. Write tonight's "
                   "journal in 4-6 plain sentences for the operator, using ONLY the facts "
                   "below. Lead with any problem and what to do about it. Do not invent "
                   "numbers, trades or causes.\n\nFacts:\n" + json.dumps(facts, indent=1))
            try:
                prose = str((await chat_json(ask, SCHEMA, client)).get("journal", ""))[:1500]
            except LLMError as exc:
                prose = f"_Gemma unavailable: {exc}_"
    text = render(findings, shares, books, prose)
    JOURNAL.mkdir(parents=True, exist_ok=True)
    (JOURNAL / f"{date.today().isoformat()}.md").write_text(text, encoding="utf-8")
    bookorders.log("desk_journal", "", json.dumps(
        {"problems": [f.text for f in findings if f.level == "problem"],
         "watch": [f.text for f in findings if f.level == "watch"]}), POLICY)
    print(text)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--no-llm", action="store_true")
    raise SystemExit(asyncio.run(main(not p.parse_args().no_llm)))
