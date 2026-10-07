"""Screen the names for Gemma's list (watchlists/gemma.txt).

    python scripts/screen_gemma_list.py           # show the screen, change nothing
    python scripts/screen_gemma_list.py --write   # rewrite watchlists/gemma.txt

A ROLLING list (operator, 2026-10-07): names stay while they still qualify,
new ones are added, and only names that stopped qualifying are removed.

  KEEP   a name already on the list if it is still an S&P 500 member, price
         >= $20, positive 12-1 momentum, and within the 300 most traded
         (looser than the entry bar, so a name does not churn in and out)
  ADD    the entry screen (the first list's rule, WORKLIST §42): the 150 most
         traded S&P 500 names, per GICS sector the strongest positive 12-1
         momentum, up to two per sector
  NEVER  anything on picks.txt, core.txt or the momentum rule's current picks
         (they reach Gemma by their own route), or crypto proxies
Removing a name from this list never sells it: Gemma's own holdings always
stay on its menu.

Commit gemma.txt after every --write: each list must trace to the trades it led to.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import opinions
from scanner.advisor.scan_watch import operator_names
from scanner.execution import picks
from scanner.features.bars import fetch_many
from scanner.features.technicals import compute
from scanner.sources import composite
from scanner.sources.index_history import SP500

LIST = Path("watchlists/gemma.txt")
SKIP = {"COIN", "MSTR"}          # crypto proxies; the desk trades stocks only
MOST_TRADED, KEEP_WITHIN, PER_SECTOR, MIN_PRICE = 150, 300, 2, 20.0


async def screen() -> tuple[list[tuple[str, str, float, float]], dict]:
    async with httpx.AsyncClient(timeout=60) as client:
        members = await composite.load_universe(client, (SP500,))
        sector = {m.symbol: m.sector for m in members}
        bars, _ = await fetch_many(tuple(sector), client, range_="2y")
    taken = (set(picks.read_list()[0]) | set(opinions.momentum_picks()[1])
             | set(operator_names(Path("watchlists/core.txt"))) | SKIP)
    rows = []
    for s, b in bars.items():
        if s in taken or len(b.closes) < 260 or b.closes[-1] < MIN_PRICE:
            continue
        mom = compute(b).momentum_12_1
        if mom is None or mom <= 0:
            continue
        dollar_volume = sum(c * v for c, v in zip(b.closes[-63:], b.volumes[-63:])) / 63
        rows.append((s, sector.get(s, "?"), mom, dollar_volume))
    ranked = sorted(rows, key=lambda r: -r[3])
    liquid = ranked[:MOST_TRADED]
    by_sector = defaultdict(list)
    for r in sorted(liquid, key=lambda r: -r[2]):
        by_sector[r[1]].append(r)
    entry = [r for sec in sorted(by_sector) for r in by_sector[sec][:PER_SECTOR]]
    keepable = {r[0]: r for r in ranked[:KEEP_WITHIN]}
    return entry, keepable


def render(chosen) -> str:
    lines = ["# GEMMA'S LIST -- extra names Gemma's book may choose from, on top of watchlists/core.txt",
             "# and the momentum rule's picks (minus anything another book holds). WORKLIST §42.",
             f"# {date.today()}: scripts/screen_gemma_list.py (rolling) -- names stay while still",
             f"# S&P 500, >= ${MIN_PRICE:.0f}, positive 12-1 momentum and in the {KEEP_WITHIN} most traded;",
             f"# new ones from the {MOST_TRADED} most traded, up to {PER_SECTOR} per sector by 12-1 momentum;",
             "# none from picks, core or the momentum rule. Commit every edit. Stocks only."]
    sector = None
    for s, sec, _, _ in chosen:
        if sec != sector:
            lines.append(f"# {sec}")
            sector = sec
        lines.append(s)
    return "\n".join(lines) + "\n"


async def main(write: bool) -> int:
    entry, keepable = await screen()
    old = set(operator_names(LIST))
    kept = [keepable[s] for s in old if s in keepable]
    added = [r for r in entry if r[0] not in old]
    chosen = sorted(kept + added, key=lambda r: (r[1], -r[2]))
    new = {s for s, *_ in chosen}
    for s, sec, mom, dv in chosen:
        mark = "" if s in old else "  NEW"
        print(f"{s:<6} {sec[:26]:<27} 12-1 {mom:+7.1f}%   ${dv / 1e9:5.2f}B/day{mark}")
    print(f"\n{len(chosen)} names; added {sorted(new - old) or 'none'}; "
          f"dropped {sorted(old - new) or 'none'}")
    if write:
        LIST.write_text(render(chosen), encoding="utf-8")
        print(f"wrote {LIST} -- commit it")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--write", action="store_true", help="rewrite watchlists/gemma.txt")
    raise SystemExit(asyncio.run(main(p.parse_args().write)))
