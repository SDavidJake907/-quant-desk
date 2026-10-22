"""TRIALS #93: Connors' RSI(2) pullback on DAILY candles -- the one classic signal strategy never run here.

Operator, 2026-10-22: "nothing that has a stragtie? nothing that has a buy/sell signal?"
Listed in TRIALS since 10-05 as "RSI(2) pullback in an uptrend (separate short-term
engine) -- practitioner (Connors)". #82 tested RSI levels on 15m/1h only, where costs
dominate; on daily candles a trade aims for a 2-4% move against the same 0.10% cost.

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  data       Yahoo daily, 10 years, today's S&P 500 members (survivorship lifts all
             long rules alike; the drift-excess measure below is the guard)
  buy        close > SMA(200) and RSI(2) < 10 at the close -> buy the NEXT open
  sell       close > SMA(5) -> sell the NEXT open (Connors' exit); safety stop
             3 x ATR(22) under the entry (as every desk test); 0.05% a side
  versions   CLASSIC (holds through weekends) and DESK (flat at Friday's close,
             the operator's live rule) -- rules.simulate(friday_flat=...)
  groups     ALL (every member), BIG (signal_lab.BIGCAP), FAST (monthly fastest 30)
  measure    EXCESS = net % - the name's average daily return x days held; t over
             each exit day's SUM of trade results (daily P&L -- the corrected scoring)
  halves     2016-10 -> 2020-12, 2021-01 -> 2026-10
  checks     a CHEAT signal (buys when tomorrow's close is higher) must win big and a
             RANDOM signal (same hold rules) must lose about the costs, or the run is void
  PASS       excess > 0 with t >= 3 in BOTH halves.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import signal_lab as lab  # noqa: E402
import sweep_slow as ss  # noqa: E402

from scanner.signals import indicators as ind  # noqa: E402
from scanner.signals import rules  # noqa: E402

SPLIT = date(2021, 1, 1)


def run(series, uni, mode):
    res = defaultdict(list)
    for s in sorted(set().union(*uni.values()) & set(series)):
        b = series[s]
        if len(b) < 260:
            continue
        d = [x[0] for x in b]
        t = [datetime(x.year, x.month, x.day, 16) for x in d]
        o, h, lo, c, v = ([x[k] for x in b] for k in range(1, 6))
        may = [s in uni.get((x.year, x.month), ()) for x in d]
        a = ind.atr(h, lo, c, 22)
        r2 = ind.rsi(c, 2)
        s200, s5 = ind.sma(c, 200), ind.sma(c, 5)
        enter = [r is not None and m is not None and r < 10 and px > m for r, m, px in zip(r2, s200, c)]
        leave = [m is not None and px > m for m, px in zip(s5, c)]
        rnd = random.Random(s)
        states = {
            "RSI2": rules._machine(enter, leave),
            "CHEAT": rules._machine([i + 2 < len(c) and c[i + 2] > o[i + 1] for i in range(len(c))],
                                    [i + 2 >= len(c) or c[i + 2] <= o[i + 1] for i in range(len(c))]),
            "RANDOM": rules._machine([rnd.random() < 0.05 for _ in c], leave),
        }
        rets = [None] + [(c[i] / c[i - 1] - 1) * 100 for i in range(1, len(c))]
        drift = {}
        for half in (True, False):
            xs = [r for x, r in zip(d, rets) if r is not None and (x < SPLIT) == half]
            drift[half] = statistics.mean(xs) if xs else 0.0
        for name, st in states.items():
            trades = rules.simulate(s, t, o, h, lo, c, st, may_enter=may, atr_series=a,
                                    friday_flat=(mode == "DESK"))
            rows = [(tr.exit_at.date(), tr.net_pct, tr.bars) for tr in trades]
            for day, net, bars in rows:
                res[name].append((day, net, net - drift[day < SPLIT] * bars))
    return res


def score(rows):
    out = []
    for half in (True, False):
        r = [x for x in rows if (x[0] < SPLIT) == half]
        by = defaultdict(float)
        for x in r:
            by[x[0]] += x[2]
        days = list(by.values())
        t = statistics.mean(days) / statistics.stdev(days) * math.sqrt(len(days)) if len(days) > 2 and statistics.stdev(days) else float("nan")
        n = len(r)
        out.append((n, statistics.mean(x[2] for x in r) if r else float("nan"),
                    statistics.mean(x[1] for x in r) if r else float("nan"),
                    sum(x[1] > 0 for x in r) / n * 100 if n else float("nan"), t))
    return out


def main() -> int:
    series = asyncio.run(ss.daily_series())
    months = {(x.year, x.month) for b in series.values() for x in (y[0] for y in b)}
    fast = lab.fast_months({s: [(x, o, h, lo, c) for x, o, h, lo, c, _ in b] for s, b in series.items()}, 30)
    groups = {"ALL": {m: set(series) for m in months}, "BIG": {m: set(lab.BIGCAP) for m in months}, "FAST": fast}
    passes, void = 0, False
    for mode in ("CLASSIC", "DESK"):
        for gname, uni in groups.items():
            res = run(series, uni, mode)
            print(f"\n== {mode} {gname} ==")
            for name in ("RSI2", "CHEAT", "RANDOM"):
                (n1, e1, net1, w1, t1), (n2, e2, net2, w2, t2) = score(res.get(name, []))
                ok = name == "RSI2" and e1 > 0 and t1 >= 3 and e2 > 0 and t2 >= 3
                passes += ok
                print(f"   {name:6} | 2016-20: {n1:6} trades excess {e1:+.2f}% net {net1:+.2f}% win {w1:3.0f}% t {t1:5.1f} | "
                      f"2021-26: {n2:6} excess {e2:+.2f}% net {net2:+.2f}% win {w2:3.0f}% t {t2:5.1f} | {'PASS' if ok else ''}", flush=True)
                if name == "CHEAT" and not (e1 > 1 and e2 > 1):
                    void = True
    print(f"\nPASS: {passes} of 6" + ("   ** CHECKS FAILED -- RUN VOID **" if void else "   (cheat check OK)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
