"""Morning ideas: earnings winners, estimates rising, and the fast-mover shadow
pool (rules in scanner/ops/ideas.py).

    python scripts/morning_ideas.py            # write logs/ideas/YYYY-MM-DD.md, record the lists
    python scripts/morning_ideas.py --no-write # print only, record nothing

Weekdays 04:40 AK (stock-scanner-ideas), before the leaders list and Gemma.
REPORT ONLY: it never edits picks.txt, never sends an order, and Gemma does
not see it. Every list is graded forward vs the S&P 500 at 5, 21 and 60
sessions from data/ideas/picks.jsonl.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import collect_earnings  # scripts/ is on the path when this script runs

from scanner.config import settings
from scanner.execution import ownership
from scanner.execution.alpaca import Alpaca
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import EXCHANGE_TZ
from scanner.ops import ideas
from scanner.sources import composite, yahoo_fundamentals
from scanner.sources.index_history import SP400, SP500

OUT = Path("logs/ideas")
RECORD = Path("data/ideas/picks.jsonl")
LOOKBACK_WEEKDAYS = 5
SHOW = 8


def completed(bars, today: date):
    """Drop a bar dated today: before or during the session it is still forming."""
    if bars.dates and bars.dates[-1] >= today:
        return bars.upto(len(bars) - 2)
    return bars


def closes(bars) -> list[tuple[date, float]]:
    return list(zip(bars.dates, bars.closes))


def triples(bars) -> list[tuple]:
    return list(zip(bars.dates, bars.opens, bars.closes))


def load_records() -> list[dict]:
    if not RECORD.exists():
        return []
    out = []
    for line in RECORD.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec.get("lists"), dict):
            out.append(rec)
    return out


async def held_by_book(client) -> dict[str, str]:
    if not settings.alpaca_key_id:
        return {}
    broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
    positions = await broker._get("/v2/positions")
    owner_map = ownership.owners(await broker._get("/v2/orders", status="all", limit=500, nested="true"))
    return {p["symbol"]: ownership.owner(p["symbol"], owner_map) for p in positions}


async def earnings_list(client, universe: set[str], spx, today: date) -> list[ideas.EarningsPick]:
    days, d = [], today - timedelta(days=1)
    while len(days) < LOOKBACK_WEEKDAYS:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    events = []
    for day in days:
        rows = await collect_earnings.fetch_day(client, day) or []
        events += [(r["symbol"], day, ideas.surprise(r)) for r in rows if r["symbol"] in universe]
    syms = tuple({s for s, _, sp in events if sp is not None and sp >= ideas.MIN_SURPRISE_PCT})
    bars, _ = await fetch_many(syms, client, range_="3mo") if syms else ({}, [])
    bench = closes(spx)
    ears = {}
    for s, day, _ in events:
        b = bars.get(s)
        if b is not None:
            got = ideas.ear(closes(completed(b, today)), bench, day)
            if got is not None:
                ears[(s, day)] = got
    return ideas.earnings_winners(events, ears, spx.dates[-1])


def fast_stats(b) -> ideas.FastStats | None:
    """Speed (20-day ATR % of the last close), 20-day dollar volume, trend."""
    n = ideas.FAST_ATR_DAYS
    if len(b.closes) < max(50, n + 1):
        return None
    c, h, lo, v = b.closes, b.highs, b.lows, b.volumes
    tr = [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
          for i in range(len(c) - n, len(c))]
    return ideas.FastStats(atr_pct=sum(tr) / n / c[-1] * 100,
                           dollar_vol=sum(c[i] * v[i] for i in range(len(c) - n, len(c))) / n,
                           above_50d=c[-1] > sum(c[-50:]) / 50)


async def daily_lists(client, universe: list[str], today: date):
    """(estimates rising, fast up, fast all) from one fetch of the universe."""
    bars, _ = await fetch_many(tuple(universe), client, range_="1y")
    stats = {}
    for s, b in bars.items():
        got = fast_stats(completed(b, today))
        if got is not None:
            stats[s] = got
    above = {s: f.above_50d for s, f in stats.items()}
    funds = await yahoo_fundamentals.fetch_many([s for s in universe if above.get(s)])
    fast_up, fast_all = ideas.fast_movers(stats)
    return ideas.estimates_rising(funds, above), fast_up, fast_all


async def grades(client, records: list[dict]) -> list[ideas.Grade]:
    if not records:
        return []
    syms = tuple(ideas.symbols_needed(records))
    bars, _ = await fetch_many(syms, client, range_="1y")
    spx = await fetch_bars("^GSPC", client, range_="1y")
    tb = {s: triples(b) for s, b in bars.items()}
    return [ideas.grade(records, tb, triples(spx), h) for h in ideas.HORIZONS]


def render(today: date, last: date, winners, rising, fast, held: dict[str, str], graded) -> str:
    def owner(s):
        return f" (held: {held[s]})" if s in held else ""

    lines = [f"# Morning ideas {today}", "",
             "REPORT ONLY -- nothing trades on these lists and Gemma does not see them. "
             "Rules fixed 2026-10-21 in scanner/ops/ideas.py; graded vs the S&P 500 below.", "",
             f"## Earnings winners (reported, beat by {ideas.MIN_SURPRISE_PCT:g}%+, "
             f"and the market rewarded it through {last})", ""]
    if winners:
        lines += ["| # | Stock | Reported | Surprise | Reaction vs S&P |", "|---|---|---|---|---|"]
        lines += [f"| {i} | {p.symbol}{owner(p.symbol)} | {p.day:%a %m/%d} | {p.surprise_pct:+.0f}% | "
                  f"{p.ear_pct:+.1f}% |" for i, p in enumerate(winners[:SHOW], 1)]
    else:
        lines.append("None today.")
    lines += ["", f"## Estimates rising (this year's EPS estimate +{ideas.MIN_REVISION_PCT:g}%+ in 90 days, "
                  f"{ideas.MIN_UP}+ analysts up and none down in 30 days, above the 50-day)", ""]
    if rising:
        lines += ["| # | Stock | Estimate, 90 days | Analysts up / down (30d) |", "|---|---|---|---|"]
        lines += [f"| {i} | {p.symbol}{owner(p.symbol)} | {p.revision_pct:+.1f}% | {p.up_30d} / {p.down_30d} |"
                  for i, p in enumerate(rising[:SHOW], 1)]
    else:
        lines.append("None today.")
    fast_up, fast_all = fast
    lines += ["", f"## Lotto trials -- fast-mover shadow pool, TRIALS #79 (20-day daily range, "
                  f"${ideas.FAST_MIN_DOLLAR_VOL / 1e6:.0f}M+/day)", "",
              "Fast AND above the 50-day (fast_up) vs fastest regardless of trend (fast_all, the control).", ""]
    for name, picks in (("fast_up", fast_up), ("fast_all", fast_all)):
        lines.append(f"**{name}:** " + (", ".join(
            f"{p.symbol}{owner(p.symbol)} {p.atr_pct:.1f}%/day" + ("" if p.above_50d else " (downtrend)")
            for p in picks[:SHOW]) or "none today."))
        lines.append("")
    lines += [f"The first {ideas.PICK_N} of each list are recorded and graded.", "", "## Track record", ""]
    if not any(g.excess[n].days for g in graded for n in ideas.LISTS):
        lines.append("Nothing graded yet: the first grades land 5 sessions after the first list.")
    for g in graded:
        cells = [f"{n}: {s.mean:+.2f}% vs S&P over {s.days} day(s), t {s.t:.1f}"
                 for n in ideas.LISTS for s in [g.excess[n]] if s.days]
        if cells:
            lines.append(f"- {g.horizon} sessions: " + "; ".join(cells))
    return "\n".join(lines) + "\n"


async def main(write: bool) -> int:
    today = datetime.now(EXCHANGE_TZ).date()
    async with httpx.AsyncClient(timeout=120) as client:
        members = await composite.load_universe(client, (SP500, SP400))
        universe = sorted({m.symbol for m in members})
        spx = completed(await fetch_bars("^GSPC", client, range_="3mo"), today)
        winners = await earnings_list(client, set(universe), spx, today)
        rising, fast_up, fast_all = await daily_lists(client, universe, today)
        held = await held_by_book(client)
        records = load_records()
        if write and not any(r["day"] == today.isoformat() for r in records):
            rec = {"day": today.isoformat(), "at": datetime.now().astimezone().isoformat(),
                   "lists": {"earnings": [p.symbol for p in winners[:ideas.PICK_N]],
                             "estimates": [p.symbol for p in rising[:ideas.PICK_N]],
                             "fast_up": [p.symbol for p in fast_up[:ideas.PICK_N]],
                             "fast_all": [p.symbol for p in fast_all[:ideas.PICK_N]]}}
            RECORD.parent.mkdir(parents=True, exist_ok=True)
            with RECORD.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            records.append(rec)
        graded = await grades(client, records)
    text = render(today, spx.dates[-1], winners, rising, (fast_up, fast_all), held, graded)
    print(text)
    if write:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{today.isoformat()}.md").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--no-write", action="store_true", help="print only, record nothing")
    raise SystemExit(asyncio.run(main(not p.parse_args().no_write)))
