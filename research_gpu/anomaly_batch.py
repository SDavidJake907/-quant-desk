"""TRIALS #102-#105: four documented market effects never tested on this desk.

Operator, 2026-10-22: "KEEP TESTING UNTIL YOU FIND SOMETHING THAT WORKS!!" -- chosen from published,
explained effects (calendar / event / sector), not indicators.

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROWS, BEFORE THE FIRST RUN.
#102 TURN OF THE MONTH (Ariel 1987; Lakonishok & Smidt 1988; McConnell & Xu 2008)
     SPY daily (30y). TOM days = the last trading day of a month + the first 3 of the next.
     Strategy: hold SPY only on TOM days (buy at the close before, sell at the close of the 3rd
     day), 0.05% a side = 0.10% a month spread over its 4 days. Effect = mean TOM-day return
     after that cost minus the mean of all other days.
#103 PRE-HOLIDAY (Ariel 1990)
     SPY daily (30y). A pre-holiday day = a trading day whose next trading day is not the next
     weekday (and not a Friday->Monday weekend). Hold only that day (0.10% round trip).
     Effect = mean pre-holiday return after cost minus the mean of all other days.
#104 EARNINGS ANNOUNCEMENT PREMIUM (Frazzini & Lamont 2007)
     data/earnings 2016-2026 (Nasdaq dates, time unknown), today's S&P 500 members, Yahoo daily.
     Buy at the close 6 sessions before the listed date, sell at the close of the session after
     it (covers before-open and after-close reports). Excess = stock minus SPY over the same days,
     minus the name's average 7-session excess over all windows (its drift), less 0.10% cost.
     t over announcement weeks (each week's excesses averaged... summed: daily-P&L rule -> summed
     per exit day).
#105 SECTOR MOMENTUM (Moskowitz & Grinblatt 1999; Faber 2010)
     The 11 SPDR sector ETFs (XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC; each once it exists),
     Yahoo monthly closes. Each month end: hold the 3 with the best 12-1 month return, equal
     weight, next month; 0.10% for each switch. Effect = monthly return minus SPY's.
HALVES: #102/#103 1996-2010 / 2011-2026; #104 2016-2020 / 2021-2026; #105 1999-2012 / 2013-2026.
PASS (each): effect > 0 in BOTH halves AND t >= 3 over the full sample (Welch for #102/#103;
             one-sample for #104 over exit days and #105 over months). Four tests at once.
CHECKS: #104 also reports a RANDOM-date version (same names, same 7-session windows) that must
        come out near zero, or #104 is void.
READ-ONLY research.
"""

from __future__ import annotations

import asyncio
import csv
import math
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import sweep_slow as ss  # noqa: E402

from scanner.features.bars import fetch_bars, fetch_many  # noqa: E402

SECTORS = ("XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC")


def tstat(xs):
    return statistics.mean(xs) / statistics.stdev(xs) * math.sqrt(len(xs)) if len(xs) > 2 and statistics.stdev(xs) else float("nan")


def welch(a, b):
    va, vb = statistics.variance(a), statistics.variance(b)
    return (statistics.mean(a) - statistics.mean(b)) / math.sqrt(va / len(a) + vb / len(b))


def verdict(e1, e2, t):
    return "PASS" if e1 > 0 and e2 > 0 and t >= 3 else "FAIL"


async def load():
    async with httpx.AsyncClient(timeout=120) as c:
        spy = await fetch_bars("SPY", c, range_="30y")
        sectors, _ = await fetch_many(SECTORS, c, range_="30y")
    return spy, sectors


def calendar_tests(spy):
    d, c = spy.dates, spy.closes
    rets = {d[i]: (c[i] / c[i - 1] - 1) * 100 for i in range(1, len(c))}
    days = sorted(rets)
    # TOM: last trading day of month + first 3 of the next
    tom = set()
    for i in range(1, len(days)):
        if days[i].month != days[i - 1].month:
            tom.add(days[i - 1])
            tom.update(days[i:i + 3])
    # pre-holiday
    pre = set()
    for a, b in zip(days, days[1:]):
        nxt_weekday = a + timedelta(days=3 if a.weekday() == 4 else 1)
        if b != nxt_weekday:
            pre.add(a)
    out = {}
    for name, sel, cost in (("#102 turn of the month", tom, 0.10 / 4), ("#103 pre-holiday", pre, 0.10)):
        res = []
        for half in ((date(1990, 1, 1), date(2011, 1, 1)), (date(2011, 1, 1), date(2100, 1, 1))):
            a = [rets[x] - cost for x in days if x in sel and half[0] <= x < half[1]]
            b = [rets[x] for x in days if x not in sel and half[0] <= x < half[1]]
            res.append((statistics.mean(a) - statistics.mean(b), len(a)))
        a = [rets[x] - cost for x in days if x in sel]
        b = [rets[x] for x in days if x not in sel]
        t = welch(a, b)
        yearly = statistics.mean(a) * len(a) / (len(days) / 252)
        print(f"{name:26} | 1996-2010: {res[0][0]:+.3f}%/day vs other days ({res[0][1]} days) | "
              f"2011-26: {res[1][0]:+.3f}% ({res[1][1]} days) | Welch t {t:.1f} | "
              f"holding only those days earns {yearly:+.1f}%/yr after costs | {verdict(res[0][0], res[1][0], t)}")
        out[name] = (res, t)
    return out


def earnings_premium(spy, series):
    spx = {d: c for d, c in zip(spy.dates, spy.closes)}
    events = defaultdict(set)
    for f in sorted((ROOT / "data/earnings").glob("20*.csv")):
        if int(f.stem) < 2016:
            continue
        with f.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["symbol"] in series:
                    events[r["symbol"]].add(date.fromisoformat(r["date"]))
    rows, rnd_rows = [], []
    rnd = random.Random(104)
    for s, b in series.items():
        if s not in events:
            continue
        d, c = b[0] if False else ([x[0] for x in b], [x[4] for x in b])
        idx = {x: i for i, x in enumerate(d)}
        def window(i0, i1):
            if i0 < 0 or i1 >= len(d) or d[i0] not in spx or d[i1] not in spx:
                return None
            return (c[i1] / c[i0] - 1 - (spx[d[i1]] / spx[d[i0]] - 1)) * 100
        all_w = [w for w in (window(i, i + 7) for i in range(0, len(d) - 7, 3)) if w is not None]
        drift = statistics.mean(all_w) if all_w else 0.0
        for e in events[s]:
            i = idx.get(e)
            if i is None:
                nxt = [k for k in range(len(d)) if d[k] >= e][:1]
                if not nxt:
                    continue
                i = nxt[0]
            w = window(i - 6, i + 1)
            if w is not None:
                rows.append((d[i + 1], w - drift - 0.10))
            j = rnd.randrange(7, len(d) - 2)
            w2 = window(j - 6, j + 1)
            if w2 is not None:
                rnd_rows.append((d[j + 1], w2 - drift - 0.10))

    def score(rs):
        by = defaultdict(float)
        for x, v in rs:
            by[x] += v
        h1 = [v for x, v in rs if x < date(2021, 1, 1)]
        h2 = [v for x, v in rs if x >= date(2021, 1, 1)]
        return statistics.mean(h1), statistics.mean(h2), tstat(list(by.values())), len(rs)

    a1, a2, t, n = score(rows)
    r1, r2, rt, rn = score(rnd_rows)
    print(f"#104 earnings premium      | 2016-20: {a1:+.3f}% a trade vs SPY & drift | 2021-26: {a2:+.3f}% | {n} trades | "
          f"t {t:.1f} | {verdict(a1, a2, t)}")
    print(f"     random-date check     | 2016-20: {r1:+.3f}% | 2021-26: {r2:+.3f}% | t {rt:.1f} "
          f"({'OK' if abs(rt) < 2 else 'CHECK FAILED -- #104 void'})")


def sector_momentum(spy, sectors):
    def monthly(b):
        m = {}
        for x, cl in zip(b.dates, b.closes):
            m[(x.year, x.month)] = cl
        return m
    mon = {s: monthly(b) for s, b in sectors.items()}
    sp = monthly(spy)
    months = sorted(sp)
    held: list[str] = []
    ex = []
    for k in range(13, len(months) - 1):
        m, nm = months[k], months[k + 1]
        scores = []
        for s, mm in mon.items():
            if m in mm and months[k - 12] in mm and months[k - 1] in mm and nm in mm:
                scores.append((mm[months[k - 1]] / mm[months[k - 12]] - 1, s))
        if len(scores) < 5:
            continue
        top = [s for _, s in sorted(scores, reverse=True)[:3]]
        switches = len(set(top) - set(held))
        held = top
        r = statistics.mean(mon[s][nm] / mon[s][m] - 1 for s in top) * 100 - switches * 0.10 / 3
        ex.append((nm, r - (sp[nm] / sp[m] - 1) * 100))
    h1 = [v for (y, _), v in ex if y < 2013]
    h2 = [v for (y, _), v in ex if y >= 2013]
    t = tstat([v for _, v in ex])
    print(f"#105 sector momentum top 3 | 1999-2012: {statistics.mean(h1):+.2f}%/month vs SPY ({len(h1)} months) | "
          f"2013-26: {statistics.mean(h2):+.2f}% ({len(h2)}) | {statistics.mean([v for _, v in ex]) * 12:+.1f}%/yr | t {t:.1f} | "
          f"{verdict(statistics.mean(h1), statistics.mean(h2), t)}")


def main() -> int:
    spy, sectors = asyncio.run(load())
    print(f"SPY {spy.dates[0]} -> {spy.dates[-1]}; sectors: " + ", ".join(f"{s} {b.dates[0].year}" for s, b in sectors.items()))
    calendar_tests(spy)
    sector_momentum(spy, sectors)
    earnings_premium(spy, asyncio.run(ss.daily_series()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
