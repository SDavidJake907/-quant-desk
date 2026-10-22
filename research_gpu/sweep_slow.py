"""TRIALS #87: the liquidity sweep on SLOW candles (1h, daily), held for days.

Operator, 2026-10-22: "keep trying something until we find with a win". Why this
one: the sweep was the only entry that beat random entries (#85, +0.02-0.05% a
trade), but 0.10% round-trip costs swamp it on 5m/15m candles. On hourly and
daily candles the same pattern trades much bigger moves for the same cost.

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  data       1h: data/intraday_alpaca 15m grouped from 9:30 (501 S&P, 2024-09 ->
             2026-09); daily: Yahoo 10y daily bars of today's S&P 500 members
  groups     BIG (signal_lab.BIGCAP) and FAST (monthly fastest, signal_lab.fast_months)
  signal     candle i: low < L < close for L = the lowest low of the 20 candles
             before (the swing low), volume >= 2 x their average
  entry      the OPEN of candle i+1
  exits      stop = low[i] - 0.1 x ATR(14); target = entry + 2R (R = entry - stop);
             time stop: out at the close of the HOLD-th candle (1h: 35 = about a
             week; daily: 10); within a candle the stop is checked first; a gap
             through the stop fills at the open. Weekends are held (slow trades).
  control    for every sweep trade, one RANDOM entry on the same name within the
             same 20 candles (seeded), no signal, same risk %, same exits
  costs      0.05% a side
  halves     1h: split 2025-09-01; daily: split 2021-01-01
  PASS       (STRICTER -- ~800 versions tested so far) mean net a trade > 0 with
             t >= 3 over exit days in BOTH halves, AND above the control in both.
READ-ONLY research.
"""

from __future__ import annotations

import asyncio
import math
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import signal_lab as lab  # noqa: E402

from scanner.features.bars import fetch_many  # noqa: E402
from scanner.signals import indicators as ind  # noqa: E402
from scanner.signals import rules  # noqa: E402
from scanner.sources import composite  # noqa: E402
from scanner.sources.index_history import SP500  # noqa: E402

N, SURGE = 20, 2.0
HOLD = {"1h": 35, "daily": 10}
SPLIT = {"1h": date(2025, 9, 1), "daily": date(2021, 1, 1)}


def trade(t, o, h, lo, c, e, stop, target, hold):
    entry = o[e]
    net = lambda px: ((px * (1 - rules.COST)) / (entry * (1 + rules.COST)) - 1) * 100  # noqa: E731
    for j in range(e, min(e + hold, len(c))):
        if lo[j] <= stop:
            return net(min(o[j], stop) if j > e else stop), j
        if h[j] >= target:
            return net(max(o[j], target) if j > e else target), j
    j = min(e + hold, len(c)) - 1
    return net(c[j]), j


def run(series: dict, uni: dict, tf: str) -> tuple[list, list]:
    res, ctl = [], []
    for s in sorted(set().union(*uni.values()) & set(series)):
        b = series[s]
        if len(b) < 200:
            continue
        t = [x[0] for x in b]
        o, h, lo, c, v = ([x[k] for x in b] for k in range(1, 6))
        atr = ind.atr(h, lo, c, 14)
        rnd = random.Random(f"{s}{tf}")
        busy = -1
        for i in range(N + 1, len(c) - 1):
            d = t[i] if isinstance(t[i], date) else t[i]
            if s not in uni.get((d.year, d.month), ()) or i + 1 <= busy:
                continue
            swing = min(lo[i - N:i])
            avg = sum(v[i - N:i]) / N
            if not (lo[i] < swing < c[i] and avg and v[i] >= SURGE * avg):
                continue
            e = i + 1
            stop = lo[i] - 0.1 * (atr[i] or 0.0)
            risk = o[e] - stop
            if risk <= 0:
                continue
            net, j = trade(t, o, h, lo, c, e, stop, o[e] + 2 * risk, HOLD[tf])
            day = t[j].date() if isinstance(t[j], datetime) else t[j]
            res.append((day, net))
            busy = j
            r = rnd.randrange(max(N + 1, i - N), i)
            re_ = r + 1
            rstop = o[re_] * (1 - risk / o[e])
            cn, cj = trade(t, o, h, lo, c, re_, rstop, o[re_] + 2 * (o[re_] - rstop), HOLD[tf])
            ctl.append((t[cj].date() if isinstance(t[cj], datetime) else t[cj], cn))
    return res, ctl


def stats(rows):
    by = defaultdict(list)
    for d, x in rows:
        by[d].append(x)
    days = [statistics.mean(v) for v in by.values()]
    m = statistics.mean(x for _, x in rows) if rows else float("nan")
    t = statistics.mean(days) / statistics.stdev(days) * math.sqrt(len(days)) if len(days) > 2 and statistics.stdev(days) else float("nan")
    win = sum(x > 0 for _, x in rows) / len(rows) * 100 if rows else float("nan")
    return len(rows), m, t, win


async def daily_series():
    async with httpx.AsyncClient(timeout=120) as client:
        members = await composite.load_universe(client, (SP500,))
        bars, _ = await fetch_many(tuple(sorted({m.symbol for m in members} | set(lab.BIGCAP))), client, range_="10y")
    return {s: [(d, o, h, lo, c, v) for d, o, h, lo, c, v in zip(b.dates, b.opens, b.highs, b.lows, b.closes, b.volumes)]
            for s, b in bars.items()}


def main() -> int:
    s15 = {p.name.split("__")[0]: lab.load_15m(p) for p in sorted((ROOT / "data/intraday_alpaca").glob("*__15m__session.json"))}
    stores = {"1h": {s: lab.to_hourly(b) for s, b in s15.items()}, "daily": asyncio.run(daily_series())}
    passes = 0
    for tf, series in stores.items():
        months = {(d.year, d.month) for b in series.values() for d in (x[0] for x in b)}
        if tf == "daily":
            rows = {s: [(d, o, h, lo, c) for d, o, h, lo, c, _ in b] for s, b in series.items()}
            fast = lab.fast_months(rows, 30)
        else:
            fast = lab.fast_months({s: lab.daily(b) for s, b in series.items()}, 30)
        for gname, uni in (("BIG", {m: set(lab.BIGCAP) for m in months}), ("FAST", fast)):
            res, ctl = run(series, uni, tf)
            sp = SPLIT[tf]
            day = lambda d: d if not isinstance(d, datetime) else d.date()  # noqa: E731
            a, b = [r for r in res if day(r[0]) < sp], [r for r in res if day(r[0]) >= sp]
            ca, cb = [r for r in ctl if day(r[0]) < sp], [r for r in ctl if day(r[0]) >= sp]
            A, B, CA, CB = stats(a), stats(b), stats(ca), stats(cb)
            ok = A[1] > 0 and A[2] >= 3 and B[1] > 0 and B[2] >= 3 and A[1] > CA[1] and B[1] > CB[1]
            passes += ok
            print(f"{tf:5} {gname:4} | half 1: {A[0]:5} trades {A[1]:+.2f}% t {A[2]:4.1f} win {A[3]:3.0f}% (random {CA[1]:+.2f}%) | "
                  f"half 2: {B[0]:5} {B[1]:+.2f}% t {B[2]:4.1f} win {B[3]:3.0f}% (random {CB[1]:+.2f}%) | {'PASS' if ok else 'FAIL'}", flush=True)
    print(f"\nPASS: {passes} of 4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
