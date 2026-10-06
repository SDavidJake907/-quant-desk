"""Post-earnings announcement drift: do stocks that beat, and that the market
rewarded, keep drifting up for three months?

    python scripts/pead_test.py

READ-ONLY research. Changes nothing live. Needs data/earnings (collect_earnings.py).

PRIOR EVIDENCE (not from this repo)
Ball & Brown (1968), Bernard & Thomas (1989): prices under-react to earnings
news and drift in its direction for ~60 trading days. Replicated for decades;
weaker in large caps in recent years (published, so it may have been traded
away -- this test is partly asking that). Chan, Jegadeesh & Lakonishok (1996),
Brandt et al. (2008): the announcement-day RETURN carries the drift too, and
the two together are stronger than either.

RULES -- FIXED 2026-10-05 BEFORE ANY EARNINGS DATA WAS READ OR SEEN.
Committed with the collector still running.
  universe   point-in-time S&P 500 members on the announcement date, plus
             MidCap 400 members from 2018 (its membership log is unreliable
             before; WORKLIST §31) -- the operator's chosen universe
  events     Nasdaq earnings rows with both EPS and forecast, 2010 onward;
             surprise = Nasdaq's surprise %, else (eps - forecast)/|forecast|
  day 0      first trading day on or after the listed date (release time is
             unknown, so day 0 may be before or after the news)
  EAR        abnormal announcement return: close day -1 -> close day +1,
             stock minus ^GSPC
  entry      close of day +2 (after the news under either timing)
  hold       60 trading days, exit at that close; 25bps round trip
  abnormal   stock return minus ^GSPC over the same days
  GROUPS     BEAT   surprise >= +5% AND EAR > 0   <- the hypothesis
             MISS   surprise <= -5%               (shown, long-only account)
             ALL    every event                   (the control)
PASS (decided now), ALL of:
  1  BEAT minus ALL, per entry month, mean > 0 with Newey-West t (lag 2) >= 2
  2  positive in both halves, 2010-2017 and 2018-2026
  3  BEAT's mean abnormal return after costs > 0
BEAT minus ALL is the honest number: survivorship (delisted names unpriced)
lifts both groups alike. 60-day holds from monthly cohorts overlap, hence
Newey-West. Trial #27 in TRIALS.md.
"""

from __future__ import annotations

import asyncio
import csv
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.features.bars import fetch_bars, fetch_many
from scanner.sources import index_history
from scanner.sources.index_history import SP400, SP500

sys.path.insert(0, "scripts")
from momentum_hac_test import newey_west_t  # noqa: E402

DATA = Path("data/earnings")
START, MID_FROM, SPLIT = date(2010, 1, 1), date(2018, 1, 1), date(2018, 1, 1)
BEAT, MISS, HOLD, ENTRY_DAY, COST = 5.0, -5.0, 60, 2, 0.0025


def load_events() -> list[dict]:
    seen, events = set(), []
    for path in sorted(DATA.glob("*.csv")):
        with path.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if not row["eps"] or not row["eps_forecast"]:
                    continue
                key = (row["symbol"], row["date"])
                if key in seen:
                    continue
                seen.add(key)
                eps, fc = float(row["eps"]), float(row["eps_forecast"])
                surprise = (float(row["surprise_pct"]) if row["surprise_pct"]
                            else (eps - fc) / abs(fc) * 100 if fc else None)
                if surprise is None:
                    continue
                events.append({"symbol": row["symbol"], "date": date.fromisoformat(row["date"]),
                               "surprise": surprise})
    return [e for e in events if e["date"] >= START]


def yahoo(symbol: str) -> str:
    return symbol.replace(".", "-")


def measure(e, bars, bench_index, bench) -> dict | None:
    i0 = next((i for i, d in enumerate(bars.dates) if d >= e["date"]), None)
    if i0 is None or i0 < 1 or i0 + ENTRY_DAY + HOLD >= len(bars):
        return None
    c = bars.closes
    j = {k: bench_index.get(bars.dates[i]) for k, i in
         (("pre", i0 - 1), ("post", i0 + 1), ("in", i0 + ENTRY_DAY), ("out", i0 + ENTRY_DAY + HOLD))}
    if None in j.values():
        return None
    b = bench.closes
    ear = (c[i0 + 1] / c[i0 - 1] - 1) - (b[j["post"]] / b[j["pre"]] - 1)
    ret = c[i0 + ENTRY_DAY + HOLD] / c[i0 + ENTRY_DAY] - 1 - COST
    abn = ret - (b[j["out"]] / b[j["in"]] - 1)
    return {**e, "ear": ear * 100, "abn": abn * 100, "entry": bars.dates[i0 + ENTRY_DAY]}


async def main() -> int:
    events = load_events()
    async with httpx.AsyncClient(timeout=60) as client:
        h500 = await index_history.load(client, spec=SP500)
        h400 = await index_history.load(client, spec=SP400)
        kept = []
        for e in events:
            members = h500.members_on(e["date"], collapse_share_classes=True)
            if e["date"] >= MID_FROM:
                members = members | h400.members_on(e["date"], collapse_share_classes=True)
            if e["symbol"] in members:
                kept.append(e)
        symbols = tuple(sorted({yahoo(e["symbol"]) for e in kept}))
        bench = await fetch_bars("^GSPC", client, range_="20y")
        bars, errors = await fetch_many(symbols, client, range_="20y")
    bench_index = {d: i for i, d in enumerate(bench.dates)}
    rows = [m for e in kept if (b := bars.get(yahoo(e["symbol"]))) is not None
            and (m := measure(e, b, bench_index, bench)) is not None]
    print(f"{len(events)} events with EPS and forecast since {START}; {len(kept)} in the universe; "
          f"{len(rows)} priced and measurable; {len(errors)} symbols failed to fetch")
    groups = {"BEAT": [r for r in rows if r["surprise"] >= BEAT and r["ear"] > 0],
              "MISS": [r for r in rows if r["surprise"] <= MISS],
              "ALL": rows}
    print(f"\n   {'group':<6}{'n':>7}{'60d abnormal':>15}{'win':>6}   2010-17     2018-26")
    for name, g in groups.items():
        a = [r["abn"] for r in g if r["entry"] < SPLIT]
        b = [r["abn"] for r in g if r["entry"] >= SPLIT]
        print(f"   {name:<6}{len(g):>7}{statistics.fmean(r['abn'] for r in g):>+14.2f}%"
              f"{sum(r['abn'] > 0 for r in g) / len(g) * 100:>5.0f}%   "
              f"{statistics.fmean(a) if a else float('nan'):+7.2f}%   {statistics.fmean(b) if b else float('nan'):+7.2f}%")
    month = lambda r: (r["entry"].year, r["entry"].month)
    by_all, by_beat = defaultdict(list), defaultdict(list)
    for r in rows:
        by_all[month(r)].append(r["abn"])
    for r in groups["BEAT"]:
        by_beat[month(r)].append(r["abn"])
    months = sorted(m for m in by_beat if by_all.get(m))
    gap = [statistics.fmean(by_beat[m]) - statistics.fmean(by_all[m]) for m in months]
    halves = [[g for m, g in zip(months, gap) if (m < (2018, 1)) == early] for early in (True, False)]
    t = newey_west_t(gap, 2)
    beat_abn = statistics.fmean(r["abn"] for r in groups["BEAT"])
    print(f"\n   BEAT minus ALL, per entry month: {statistics.fmean(gap):+.2f}pp  NW t {t:.2f}  "
          f"({len(months)} months)   2010-17 {statistics.fmean(halves[0]):+.2f}pp   "
          f"2018-26 {statistics.fmean(halves[1]):+.2f}pp")
    checks = (statistics.fmean(gap) > 0 and t >= 2,
              all(h and statistics.fmean(h) > 0 for h in halves),
              beat_abn > 0)
    print(f"   PASS 1 gap t>=2: {'ok' if checks[0] else 'X'}   2 both halves: {'ok' if checks[1] else 'X'}"
          f"   3 BEAT abnormal > 0 after costs: {'ok' if checks[2] else 'X'}   ->  "
          f"{'PASS' if all(checks) else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
