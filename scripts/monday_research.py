"""Monday research: refresh Gemma's list, and a research pack for the operator.

    python scripts/monday_research.py            # write the pack; refresh + commit gemma.txt
    python scripts/monday_research.py --no-write # pack only, list untouched

Mondays 04:30 AK (stock-scanner-monday), before Gemma's 05:45 decision:
  1  watchlists/gemma.txt refreshed by the rolling screen (screen_gemma_list.py)
     and committed on its own, so every Gemma trade traces to the list behind it
  2  logs/research/YYYY-MM-DD.md for the operator's own research:
       - Gemma's list: what was added and dropped
       - new leaders: strong 12-1 momentum within 5% of the 52-week high
       - pullback rebounds (the COHR profile): strong 12-1, 10-30% off the high,
         up 8%+ on the month -- an untested shape, shown as ideas
       - sector leadership: median 1-month and 3-month return per sector
       - the momentum rule's current top 10
       - your picks: trend, room to stop, earnings within 10 days
None of this trades; the operator decides what goes on picks.txt.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screen_gemma_list as sgl                                   # noqa: E402

from scanner.advisor import opinions                              # noqa: E402
from scanner.advisor.scan_watch import operator_names             # noqa: E402
from scanner.execution import picks                               # noqa: E402
from scanner.features.bars import fetch_many                      # noqa: E402
from scanner.features.technicals import compute                   # noqa: E402
from scanner.ops import weekly as w                               # noqa: E402
from scanner.sources import calendar as cal                       # noqa: E402
from scanner.sources import composite                             # noqa: E402
from scanner.sources.index_history import SP400, SP500            # noqa: E402

OUT = Path("logs/research")
SKIP = {"COIN", "MSTR"}


def ret(cl, n):
    return (cl[-1] / cl[-n - 1] - 1) * 100 if len(cl) > n else None


def commit_list() -> str:
    diff = subprocess.run(["git", "status", "--porcelain", "--", str(sgl.LIST)],
                          capture_output=True, text=True).stdout.strip()
    if not diff:
        return "gemma.txt unchanged"
    subprocess.run(["git", "add", "--", str(sgl.LIST)], check=True)
    subprocess.run(["git", "commit", "-q", "-m",
                    f"chore: Monday refresh of Gemma's list ({date.today()})", "--", str(sgl.LIST)],
                   check=True)
    return "gemma.txt refreshed and committed"


async def main(write: bool) -> int:
    old = set(operator_names(sgl.LIST))
    entry, keepable = await sgl.screen()
    kept = [keepable[s] for s in old if s in keepable]
    added = [r for r in entry if r[0] not in old]
    chosen = sorted(kept + added, key=lambda r: (r[1], -r[2]))
    new = {r[0] for r in chosen}
    status = "list not written (--no-write)"
    if write:
        sgl.LIST.write_text(sgl.render(chosen), encoding="utf-8")
        status = commit_list()

    async with httpx.AsyncClient(timeout=120) as client:
        members = await composite.load_universe(client, (SP500, SP400))
        sector = {m.symbol: m.sector for m in members}
        bars, _ = await fetch_many(tuple(sector), client, range_="2y")
        mine = picks.read_list()[0]
        # Foreign listings (ARM, TSM) are outside the S&P universe: price them too.
        missing = tuple(s for s in mine if s not in bars)
        if missing:
            extra, _ = await fetch_many(missing, client, range_="2y")
            bars.update(extra)
        try:
            earnings, _failed = await cal.upcoming_earnings(frozenset(mine), client, horizon=10)
        except Exception:                                          # noqa: BLE001
            earnings = {}
    leaders, rebounds, by_sector = [], [], defaultdict(list)
    for s, b in bars.items():
        cl = b.closes
        if s in SKIP or len(cl) < 260 or cl[-1] < 20:
            continue
        dv = sum(c * v for c, v in zip(cl[-63:], b.volumes[-63:])) / 63
        m, r1, r3 = compute(b).momentum_12_1, ret(cl, 21), ret(cl, 63)
        if None in (m, r1, r3):
            continue
        by_sector[sector.get(s, "?")].append((r1, r3))
        if dv < 2e8:
            continue
        off = (cl[-1] / max(cl[-252:]) - 1) * 100
        if m >= 80 and off >= -5:
            leaders.append((s, sector.get(s, "?"), m, r1, off))
        if m >= 80 and -30 <= off <= -10 and r1 >= 8 and w.trend_label(cl) != w.DOWN:
            rebounds.append((s, sector.get(s, "?"), m, r1, off))

    lines = [f"# Monday research pack {date.today():%A %Y-%m-%d}", "",
             "Ideas and facts for the week's picks. Nothing here trades; you decide picks.txt.", "",
             "## Gemma's list", "",
             f"- {status}; {len(chosen)} names. Added: {sorted(new - old) or 'none'}. "
             f"Dropped: {sorted(old - new) or 'none'}.", "",
             "## New leaders (12-1 momentum >= +80%, within 5% of the 52-week high, liquid)", "",
             "| Stock | Sector | 12-1 | 1 month | Off high |", "|---|---|---|---|---|"]
    lines += [f"| {s} | {sec[:24]} | {m:+.0f}% | {r1:+.1f}% | {off:+.1f}% |"
              for s, sec, m, r1, off in sorted(leaders, key=lambda x: -x[3])[:15]]
    lines += ["", "## Pullback rebounds -- the COHR profile (untested shape: ideas only)", "",
              "| Stock | Sector | 12-1 | 1 month | Off high |", "|---|---|---|---|---|"]
    lines += [f"| {s} | {sec[:24]} | {m:+.0f}% | {r1:+.1f}% | {off:+.1f}% |"
              for s, sec, m, r1, off in sorted(rebounds, key=lambda x: -x[3])[:15]]
    lines += ["", "## Sector leadership (median of S&P 500 + 400 members)", "",
              "| Sector | 1 month | 3 months | Stocks |", "|---|---|---|---|"]
    for sec, rs in sorted(by_sector.items(), key=lambda kv: -statistics.median(r[0] for r in kv[1])):
        lines.append(f"| {sec[:28]} | {statistics.median(r[0] for r in rs):+.1f}% | "
                     f"{statistics.median(r[1] for r in rs):+.1f}% | {len(rs)} |")
    lines += ["", f"## The momentum rule's current top 10: {', '.join(opinions.momentum_picks()[1])}", "",
              "## Your picks", "", "| Stock | Trend | 1 month | Earnings (10 days) |", "|---|---|---|---|"]
    for s in mine:
        b = bars.get(s)
        cl = b.closes if b else ()
        e = earnings.get(s)
        lines.append(f"| {s} | {w.trend_label(cl) if cl else '?'} | "
                     f"{f'{ret(cl, 21):+.1f}%' if cl and ret(cl, 21) is not None else '?'} | "
                     f"{e.label if e else '-'} |")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{date.today().isoformat()}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nsaved {path}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--no-write", action="store_true", help="pack only; leave gemma.txt alone")
    raise SystemExit(asyncio.run(main(not p.parse_args().no_write)))
