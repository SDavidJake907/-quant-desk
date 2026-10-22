"""TRIALS #88: the sweep + footprint ABSORPTION on 1-hour candles.

Operator, 2026-10-22, with a footprint chart (buy vs sell volume at each price):
"Yes, test it with these time. same timeframe" -- the 1h timeframe of #87.

Real footprints need every trade tagged buyer or seller (not on this Alpaca plan).
Estimated instead by BULK VOLUME CLASSIFICATION (Easley, Lopez de Prado & O'Hara
2012) from 1-minute candles: a minute's volume is buyer-initiated in proportion
Phi(dP / sigma), dP its close-to-close change, sigma the standard deviation of
1-minute changes over the prior 390 minutes.

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  data       data/research_1m (Alpaca SIP 1-minute, 2025-10-01 on), regular hours,
             grouped into hourly candles from 9:30; names as #83 (BIG fixed 20,
             FAST monthly 20)
  sweep      hour i: low < L < close, L = the lowest low of the 20 hours before;
             volume >= 2 x their average (as #87)
  absorption the minutes of hour i whose low is in the bottom QUARTER of the
             hour's range: estimated SELL share of their volume >= 0.60; and the
             hour closes in the TOP HALF of its range
  entry      the open of hour i+1; stop = low[i] - 0.1 x ATR(14); target 2R;
             out at the close of the 35th hour; stop first within an hour; 0.05%
             a side (as #87)
  versions   SWEEP (no footprint), ABSORB (sweep + absorption)
  control    FAIR random: for each ABSORB trade, 3 random hours anywhere in the
             name's eligible months, same risk %, same exits (seeded)
  halves     split 2026-04-15
  PASS       ABSORB: mean net > 0 with t >= 3 in both halves, AND above the
             random control and above SWEEP in both halves.
READ-ONLY research.
"""

from __future__ import annotations

import asyncio
import math
import random
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import scalper_test as st  # noqa: E402
import signal_lab as lab  # noqa: E402
import sweep_slow as ss  # noqa: E402

from scanner.signals import indicators as ind  # noqa: E402

N, SURGE, SELL_SHARE, HOLD = 20, 2.0, 0.60, 35
SPLIT = date(2026, 4, 15)


def phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def hourly_with_footprint(minutes):
    """-> hourly candles (t, o, h, l, c, v) and, per hour, the minutes' (low, buy share, volume)."""
    ch = [None] + [minutes[k][4] - minutes[k - 1][4] for k in range(1, len(minutes))]
    hours, inside = [], []
    window: list[float] = []
    for k, (t, o, h, lo, c, v) in enumerate(minutes):
        d = ch[k]
        sigma = statistics.pstdev(window) if len(window) >= 30 else None
        buy = phi(d / sigma) if (d is not None and sigma) else 0.5
        if d is not None:
            window.append(d)
            if len(window) > 390:
                window.pop(0)
        key = (t.date(), (t.hour * 60 + t.minute - 570) // 60)
        if hours and hours[-1][0] == key:
            _, (t0, o0, h0, l0, _, v0) = hours[-1]
            hours[-1] = (key, (t0, o0, max(h0, h), min(l0, lo), c, v0 + v))
            inside[-1].append((lo, buy, v))
        else:
            hours.append((key, (t, o, h, lo, c, v)))
            inside.append([(lo, buy, v)])
    return [x for _, x in hours], inside


def absorbed(hour, mins) -> bool:
    _, o, h, lo, c, v = hour
    rng = h - lo
    if rng <= 0:
        return False
    low_zone = [(b, vol) for m_lo, b, vol in mins if m_lo <= lo + 0.25 * rng]
    vol = sum(x for _, x in low_zone)
    if not vol:
        return False
    sell = sum((1 - b) * x for b, x in low_zone) / vol
    return sell >= SELL_SHARE and c >= lo + 0.5 * rng


async def main() -> int:
    async with httpx.AsyncClient(timeout=120) as client:
        fast = await st.fast_by_month(client)
        names = set(lab.BIGCAP) | set().union(*fast.values())
        data = await st.candles(client, names)
    groups = {"BIG": lambda s, d: s in lab.BIGCAP, "FAST": lambda s, d: s in fast.get((d.year, d.month), ())}
    passes = 0
    for gname, ok_name in groups.items():
        res: dict[str, list] = defaultdict(list)
        for s, mins in sorted(data.items()):
            hours, inside = hourly_with_footprint(mins)
            if len(hours) < 100:
                continue
            t = [x[0] for x in hours]
            o, h, lo, c, v = ([x[k] for x in hours] for k in range(1, 6))
            atr = ind.atr(h, lo, c, 14)
            eligible = [k for k in range(N + 1, len(c) - 1) if ok_name(s, t[k])]
            rnd = random.Random(f"fp{s}{gname}")
            busy = {"SWEEP": -1, "ABSORB": -1}
            for i in eligible:
                swing = min(lo[i - N:i])
                avg = sum(v[i - N:i]) / N
                if not (lo[i] < swing < c[i] and avg and v[i] >= SURGE * avg):
                    continue
                e = i + 1
                stop = lo[i] - 0.1 * (atr[i] or 0.0)
                risk = o[e] - stop
                if risk <= 0:
                    continue
                for version in ("SWEEP", "ABSORB"):
                    if version == "ABSORB" and not absorbed(hours[i], inside[i]):
                        continue
                    if e <= busy[version]:
                        continue
                    net, j = ss.trade(t, o, h, lo, c, e, stop, o[e] + 2 * risk, HOLD)
                    res[version].append((t[j].date(), net))
                    busy[version] = j
                    if version == "ABSORB":
                        for _ in range(3):
                            r = rnd.choice(eligible) + 1
                            rstop = o[r] * (1 - risk / o[e])
                            cn, cj = ss.trade(t, o, h, lo, c, r, rstop, o[r] + 2 * (o[r] - rstop), HOLD)
                            res["RANDOM"].append((t[cj].date(), cn))
        print(f"\n== 1h {gname} ==")
        halves = {k: ([r for r in v if r[0] < SPLIT], [r for r in v if r[0] >= SPLIT]) for k, v in res.items()}
        for k in ("ABSORB", "SWEEP", "RANDOM"):
            a, b = halves.get(k, ([], []))
            A, B = ss.stats(a), ss.stats(b)
            print(f"   {k:7} | half 1: {A[0]:4} trades {A[1]:+.2f}% t {A[2]:4.1f} win {A[3]:3.0f}% | "
                  f"half 2: {B[0]:4} trades {B[1]:+.2f}% t {B[2]:4.1f} win {B[3]:3.0f}%")
        (aa, ab), (sa, sb), (ra, rb) = (tuple(ss.stats(x) for x in halves.get(k, ([], []))) for k in ("ABSORB", "SWEEP", "RANDOM"))
        ok = (aa[1] > 0 and aa[2] >= 3 and ab[1] > 0 and ab[2] >= 3 and aa[1] > ra[1] and ab[1] > rb[1]
              and aa[1] > sa[1] and ab[1] > sb[1])
        passes += ok
        print(f"   ABSORB: {'PASS' if ok else 'FAIL'}")
    print(f"\nPASS: {passes} of 2")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
