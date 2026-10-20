"""TRIALS #75: when do stocks rise / drop, which indicator to sell at, which to buy at.

Operator, 2026-10-19, after #74 came closest: "lets look deeper ... by the weeks,
1-2-3-4. when does it drop. when does it rise ... when to sell at what indicator
... when the best indicator to buy".

Many slices are searched, so the design is DISCOVER then CONFIRM:
  year 1 = days before 2025-10-01 (search), year 2 = from 2025-10-01 (check).
  A slice is CONFIRMED only if year 2 has the SAME SIGN with t >= 2.
  Everything is printed; nothing is picked on year 2.

Data: data/research_5m (#74's download): 100 random S&P names (seed 74) graded;
her 7 shown. Regular-session 5-min bars, RSI(14) Wilder carried across days,
VWAP reset daily. Cost 0.05% per side wherever a trade is made.

A  Calendar, per stock-day, averaged over names per day:
   close->close, overnight (prev close -> open), day (open -> close)
   by week of month (day 1-7 = week 1, ... 29-31 = week 5) and by weekday.
   Score: the slice's mean minus the all-days mean.
B  Sell grid (as #74): first 5-min close with RSI > R and close > VWAP x (1+V),
   sell at the next open, rebuy at the close; rule minus hold.
   R in 70/75/80/85, V in 0.5/1/1.5/2%. Also #74's cell split by week of month.
C  Buy grid: first 5-min close with RSI < R and close < VWAP x (1-V), buy at the
   next open, sell at (i) the close, (ii) the next day's close. Score: that
   return minus the same stock's return from that day's OPEN over the same
   horizon (did waiting for the dip beat buying at the open?), minus costs.
   R in 20/25/30, V in 0.5/1/2%.
"""

from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DIR = Path("data/research_5m")
SPLIT = date(2025, 10, 1)
COST = 0.0005
RSI_N = 14
SELL_R, SELL_V = (70, 75, 80, 85), (0.005, 0.01, 0.015, 0.02)
BUY_R, BUY_V = (20, 25, 30), (0.005, 0.01, 0.02)
HERS = ("COHR", "MPC", "MU", "NVDA", "SNDK", "VLO", "WDC")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")


def sessions(raw):
    days = defaultdict(list)
    for t, o, h, l, c, v in raw:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(ET)
        if time(9, 30) <= dt.time() < time(16, 0):
            days[dt.date()].append((o, h, l, c, v))
    return [(d, b) for d, b in sorted(days.items()) if len(b) >= 70]


def enrich(days):
    """Per day: bars with (open, close, rsi, close/vwap - 1)."""
    ag = al = 0.0
    prev = None
    k = 0
    out = []
    for day, bars in days:
        pv = vol = 0.0
        rows = []
        for o, h, l, c, v in bars:
            if prev is not None:
                d = c - prev
                g, ls = max(d, 0.0), max(-d, 0.0)
                k += 1
                if k <= RSI_N:
                    ag += g / RSI_N
                    al += ls / RSI_N
                else:
                    ag = (ag * (RSI_N - 1) + g) / RSI_N
                    al = (al * (RSI_N - 1) + ls) / RSI_N
            prev = c
            r = (100 - 100 / (1 + ag / al) if al else 100.0) if k > RSI_N else 50.0
            pv += (h + l + c) / 3 * v
            vol += v
            rows.append((o, c, r, c / (pv / vol) - 1 if vol else 0.0))
        out.append((day, rows))
    return out


def first(rows, test):
    for i, (_o, c, r, dv) in enumerate(rows[:-1]):
        if test(r, dv):
            return i
    return None


def stock_records(days):
    """{key: {day: value}} for every measured slice of one stock."""
    rec = defaultdict(dict)
    for j in range(1, len(days) - 1):
        day, rows = days[j]
        pc = days[j - 1][1][-1][1]
        o, c = rows[0][0], rows[-1][1]
        nc = days[j + 1][1][-1][1]
        hold = c / pc - 1
        rec["cc"][day] = hold
        rec["on"][day] = o / pc - 1
        rec["oc"][day] = c / o - 1
        for R in SELL_R:
            for V in SELL_V:
                i = first(rows, lambda r, dv: r > R and dv > V)
                rec[("sell", R, V)][day] = 0.0 if i is None else \
                    (rows[i + 1][0] - c) / pc - 2 * COST
        for R in BUY_R:
            for V in BUY_V:
                i = first(rows, lambda r, dv: r < R and dv < -V)
                if i is None:
                    continue
                px = rows[i + 1][0]
                rec[("buy0", R, V)][day] = (c / px - 1) - (c / o - 1)
                rec[("buy1", R, V)][day] = (nc / px - 1) - (nc / o - 1)
                rec[("buy0raw", R, V)][day] = c / px - 1 - 2 * COST
    return rec


def stats(xs):
    n = len(xs)
    if n < 3:
        return 0.0, 0.0, n
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, (m / (sd / math.sqrt(n)) if sd else 0.0), n


def by_day(recs, key, keep=lambda d: True, minus=None):
    """Daily average across stocks of rec[key] (minus that day's mean of `minus`)."""
    agg = defaultdict(list)
    base = defaultdict(list)
    for r in recs:
        for d, v in r.get(key, {}).items():
            if keep(d):
                agg[d].append(v)
        if minus:
            for d, v in r[minus].items():
                base[d].append(v)
    return {d: sum(v) / len(v) - (sum(base[d]) / len(base[d]) if minus else 0.0)
            for d, v in agg.items()}


def halves(series: dict):
    a = [v for d, v in sorted(series.items()) if d < SPLIT]
    b = [v for d, v in sorted(series.items()) if d >= SPLIT]
    return stats(a), stats(b)


def verdict(a, b):
    (ma, _, _), (mb, tb, _) = a, b
    return "CONFIRMED" if ma and mb and (ma > 0) == (mb > 0) and abs(tb) >= 2 else ""


def line(label, a, b, scale=100):
    v = verdict(a, b)
    return (f"  {label:<28} yr1 {a[0]*scale:+7.3f}% t {a[1]:+5.2f} (n {a[2]:>4}) | "
            f"yr2 {b[0]*scale:+7.3f}% t {b[1]:+5.2f} (n {b[2]:>4})  {v}")


def week_of(d: date) -> int:
    return (d.day - 1) // 7 + 1


def main() -> int:
    pool = sorted(p.name.split("__")[0] for p in Path("data/intraday_alpaca").glob("*__15m__session.json"))
    rand = random.Random(74).sample([s for s in pool if s not in HERS], 100)
    recs = {s: stock_records(enrich(sessions(json.loads((DIR / f"{s}.json").read_text()))))
            for s in [*rand, *HERS] if (DIR / f"{s}.json").exists()}
    R = [recs[s] for s in rand if s in recs]
    hers = [recs[s] for s in HERS if s in recs]
    print(f"graded: {len(R)} random names; split {SPLIT}; CONFIRMED = same sign in yr2, |t|>=2\n")

    print("A. CALENDAR -- slice mean minus the all-days mean (per stock-day)")
    for part, name in (("cc", "close->close"), ("on", "overnight"), ("oc", "open->close")):
        allm = by_day(R, part)
        print(f" {name}: all days yr1 {halves(allm)[0][0]*100:+.3f}%  yr2 {halves(allm)[1][0]*100:+.3f}%")
        m1, m2 = halves(allm)[0][0], halves(allm)[1][0]
        dev = {d: v - (m1 if d < SPLIT else m2) for d, v in allm.items()}
        for w in range(1, 6):
            print(line(f"week {w}", *halves({d: v for d, v in dev.items() if week_of(d) == w})))
        for i, wd in enumerate(WEEKDAYS):
            print(line(f"{wd}", *halves({d: v for d, v in dev.items() if d.weekday() == i})))
    print("  (% and t are the slice vs that year's average day)\n")

    print("B. SELL GRID -- first close with RSI > R and > V over VWAP; sell next open, rebuy close; minus hold")
    for Rr in SELL_R:
        for V in SELL_V:
            print(line(f"RSI>{Rr} VWAP+{V*100:.1f}%", *halves(by_day(R, ("sell", Rr, V)))))
    print(" #74's cell by week of month:")
    for w in range(1, 6):
        print(line(f"RSI>75 VWAP+1% week {w}",
                   *halves(by_day(R, ("sell", 75, 0.01), keep=lambda d, w=w: week_of(d) == w))))

    print("\nC. BUY GRID -- first close with RSI < R and > V under VWAP; buy next open")
    print("   (vs buying that day's open; 'raw' = dip buy -> close, after costs)")
    for Rr in BUY_R:
        for V in BUY_V:
            k = (Rr, V)
            print(line(f"RSI<{Rr} VWAP-{V*100:.1f}% ->close", *halves(by_day(R, ("buy0",) + k))))
            print(line(f"   ... ->next close", *halves(by_day(R, ("buy1",) + k))))
            print(line(f"   ... raw ->close", *halves(by_day(R, ("buy0raw",) + k))))

    print("\nHER 7 (not graded) -- #74 cell and the best-looking yr1 sell/buy cells, both years together")
    for s in HERS:
        if s not in recs:
            continue
        r = recs[s]
        sell = stats(list(r[("sell", 75, 0.01)].values()))
        buy = stats(list(r.get(("buy0raw", 30, 0.01), {}).values()))
        print(f"  {s:<5} sell RSI>75/+1%: {sell[0]*25200:+6.1f}%/yr vs hold | "
              f"dip buy RSI<30/-1% ->close: {buy[0]*100:+.3f}% per trade, n {buy[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
