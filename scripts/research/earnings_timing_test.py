"""Buy the day before earnings, or the morning after? (WORKLIST §55)

    python scripts/earnings_timing_test.py            # collect dates (cached), then test

READ-ONLY research. Report dates from Nasdaq's earnings calendar (the same source
the desk uses), one request per day, cached to data/earnings/history.json.
Prices: cached 5-year daily bars, current S&P 500 members (survivors: flatters
everything, both arms alike). Rules fixed before the first run:

  reaction day   pre-mkt report on D -> D; after-close report on D -> next session;
                 no time given -> excluded (cannot tell which open priced it).
  A  BEFORE      buy the close before the reaction day, sell the close 5 sessions
                 after it (through the gap, then 5 days).
  B  AFTER       buy the first open after the news is public (the reaction
                 day's open), sell the close 10 sessions later.
  gap risk       how often the reaction-day open is below a 2.5 x ATR(14) stop
                 set at the close before (the desk's picks stop).
  baseline       the same windows started on random non-event days.
AMENDED 2026-10-08 after the first run, for missing data only: Nasdaq's past
calendar gives no time for 35,161 of 35,305 reports, so the reaction day is
unknown. Both possible days are covered instead:
  A  buy the close before the report date; sell the close 6 sessions after it.
  B  buy the open 2 sessions after the report date (the news is public either
     way); sell the close 10 sessions later.
  gap risk  the lower of the two possible reaction-day opens vs the stop.
(The first run, 26 timed reports, was too small to steer anything.)
Split: all events vs strong momentum (12-1 return > +40% at the close before).
Costs: 0.10% round trip.
"""

from __future__ import annotations

import asyncio
import json
import random
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.features.bars import parse_chart
from scanner.sources.calendar import EARNINGS_URL, HEADERS

CACHE = Path("data/earnings/history.json")
START, END = date(2024, 10, 1), date(2026, 9, 30)
COST = 0.10
STOP_ATR = 2.5


async def collect() -> dict[str, list[list[str]]]:
    have = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    days = [START + timedelta(days=k) for k in range((END - START).days + 1)]
    todo = [d for d in days if d.weekday() < 5 and d.isoformat() not in have]
    async with httpx.AsyncClient(timeout=20) as c:
        for n, d in enumerate(todo, 1):
            try:
                r = await c.get(EARNINGS_URL, params={"date": d.isoformat()}, headers=HEADERS)
                rows = (r.json().get("data") or {}).get("rows") or []
                have[d.isoformat()] = [[str(x.get("symbol", "")).upper(), str(x.get("time", "")).lower()]
                                       for x in rows]
            except Exception:                              # noqa: BLE001
                continue
            if n % 50 == 0:
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps(have))
                print(f"  {n}/{len(todo)} days")
            await asyncio.sleep(0.3)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(have))
    return have


def load_prices() -> dict[str, tuple[list[date], list[float], list[float], list[float], list[float]]]:
    sp = {l.split(",")[0].strip() for l in Path("data/constituents.csv").read_text().splitlines()[1:]}
    out = {}
    for f in Path("data/bars").glob("*__5y__1d.json"):
        s = f.name.split("__")[0]
        if s not in sp:
            continue
        try:
            b = parse_chart(json.loads(f.read_text())["payload"], s)
        except Exception:                                  # noqa: BLE001
            continue
        out[s] = (list(b.dates), list(b.opens), list(b.highs), list(b.lows), list(b.closes))
    return out


def summarise(name: str, xs: list[float]) -> None:
    if not xs:
        print(f"  {name:44s} n=0")
        return
    m, sd = statistics.fmean(xs), statistics.pstdev(xs) or 1
    win = sum(1 for x in xs if x > 0) / len(xs)
    xs_sorted = sorted(xs)
    print(f"  {name:44s} n={len(xs):5d}  mean {m:+.2f}%  t {m / sd * len(xs) ** .5:+.1f}  "
          f"win {win:.0%}  worst 5% {xs_sorted[len(xs) // 20]:+.1f}%")


def main() -> int:
    hist = asyncio.run(collect())
    prices = load_prices()
    idx = {s: {d: i for i, d in enumerate(p[0])} for s, p in prices.items()}
    res = {k: [] for k in ("A", "B", "A_mom", "B_mom", "baseA", "baseB")}
    gaps, gaps_mom = [0, 0], [0, 0]
    event_days: dict[str, set[int]] = {}
    for day, rows in hist.items():
        d = date.fromisoformat(day)
        for sym, when in rows:
            if sym not in prices:
                continue
            dates, o, h, l, c = prices[sym]
            i = idx[sym].get(d)
            if i is None:
                continue
            if i < 254 or i + 13 >= len(c):
                continue
            event_days.setdefault(sym, set()).update(range(i - 1, i + 13))
            before = i - 1
            tr = [max(h[k] - l[k], abs(h[k] - c[k - 1]), abs(l[k] - c[k - 1])) for k in range(before - 13, before + 1)]
            atr = sum(tr) / 14
            mom = c[before - 21] / c[before - 252] - 1
            a = (c[i + 6] / c[before] - 1) * 100 - COST
            b = (c[i + 12] / o[i + 2] - 1) * 100 - COST
            gapped = min(o[i], o[i + 1]) < c[before] - STOP_ATR * atr
            res["A"].append(a); res["B"].append(b)
            gaps[0] += gapped; gaps[1] += 1
            if mom > 0.40:
                res["A_mom"].append(a); res["B_mom"].append(b)
                gaps_mom[0] += gapped; gaps_mom[1] += 1
    rnd = random.Random(7)
    for sym, (dates, o, h, l, c) in prices.items():
        busy = event_days.get(sym, set())
        if len(c) < 270:                       # under a year of history: no 12-1 window
            continue
        for _ in range(20):
            r = rnd.randrange(254, len(c) - 13)
            if dates[r] < START or r in busy:
                continue
            res["baseA"].append((c[r + 6] / c[r - 1] - 1) * 100 - COST)
            res["baseB"].append((c[r + 12] / o[r + 2] - 1) * 100 - COST)
    print(f"\nS&P 500 earnings {START} .. {END}, survivors, {COST}% costs\n")
    summarise("A  buy the day BEFORE, hold through + 5 days", res["A"])
    summarise("B  buy the morning AFTER, hold 10 days", res["B"])
    summarise("   baseline: same windows, random days (A)", res["baseA"])
    summarise("   baseline: same windows, random days (B)", res["baseB"])
    print("  strong momentum only (12-1 > +40%):")
    summarise("A  before", res["A_mom"])
    summarise("B  after", res["B_mom"])
    print(f"\n  gap past a {STOP_ATR} x ATR stop at the open: all {gaps[0] / max(1, gaps[1]):.1%} "
          f"of reports; strong momentum {gaps_mom[0] / max(1, gaps_mom[1]):.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
