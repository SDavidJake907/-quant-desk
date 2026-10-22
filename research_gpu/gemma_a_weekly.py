"""TRIALS #96: Gemma's experiment A -- weekly Connors RSI oversold, only in an uptrend.

Gemma, asked 2026-10-22 to review every test (data/gemma/ask_strategy_review_2026-10-22.txt):
"Weekly Connors RSI is oversold AND the stock is in a confirmed uptrend (price above
200-day moving average) ... may filter out 'falling knives'". Operator: "Yeah".

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  data       Yahoo daily 10y, today's S&P 500 -> weekly candles (signals_slow.weekly)
  buy        weekly Connors RSI < 10 (rules.RULES["connors"] entry) AND the weekly
             close > its 40-week SMA (the 200-day average on weekly candles) ->
             next week's open
  sell       weekly Connors RSI > 70 (the same exit as #94, so the trend filter is
             the ONLY change); 3 x ATR(22) weekly stop; holds through weekends
  compare    #94's plain weekly Connors, run here alongside
  groups     ALL, BIG, FAST; excess over drift; t over daily P&L totals; cheat and
             random checks; halves 2016-20 / 2021-26
  PASS       excess > 0 with t >= 3 in BOTH halves, and above plain weekly Connors
             in both halves (the filter must add something)
READ-ONLY research.
"""

from __future__ import annotations

import asyncio
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import signal_lab as lab  # noqa: E402
import signals_slow as sw  # noqa: E402
import sweep_slow as ss  # noqa: E402

from scanner.signals import indicators as ind  # noqa: E402
from scanner.signals import rules  # noqa: E402


def run(series, uni):
    res = defaultdict(list)
    for s in sorted(set().union(*uni.values()) & set(series)):
        b = series[s]
        if len(b) < 60:
            continue
        d = [x[0] for x in b]
        t = [datetime(x.year, x.month, x.day, 16) for x in d]
        o, h, lo, c, v = ([x[k] for x in b] for k in range(1, 6))
        may = [s in uni.get((x.year, x.month), ()) for x in d]
        a = ind.atr(h, lo, c, 22)
        sma40 = ind.sma(c, 40)
        crsi = ind.connors_rsi(c)
        enter = [x is not None and x < 10 for x in crsi]
        leave = [x is not None and x > 70 for x in crsi]
        rets = [None] + [(c[i] / c[i - 1] - 1) * 100 for i in range(1, len(c))]
        drift = {}
        for half in (True, False):
            xs = [r for x, r in zip(d, rets) if r is not None and (x < sw.SPLIT) == half]
            drift[half] = statistics.mean(xs) if xs else 0.0
        rnd = random.Random(s)
        states = {
            "GEMMA A (connors + uptrend)": (rules._machine(enter, leave),
                                            [m is not None and px > m for m, px in zip(sma40, c)]),
            "plain weekly connors (#94)": (rules._machine(enter, leave), None),
            "CHEAT": (rules._machine([i + 2 < len(c) and c[i + 2] > o[i + 1] for i in range(len(c))],
                                     [i + 2 >= len(c) or c[i + 2] <= o[i + 1] for i in range(len(c))]), None),
            "RANDOM": ([1 if rnd.random() < 0.5 else 0 for _ in c], None),
        }
        for name, (st, gate) in states.items():
            for tr in rules.simulate(s, t, o, h, lo, c, st, entry_ok=gate, may_enter=may, atr_series=a,
                                     friday_flat=False):
                day = tr.exit_at.date()
                res[name].append((day, tr.net_pct, tr.net_pct - drift[day < sw.SPLIT] * tr.bars))
    return res


def main() -> int:
    daily = asyncio.run(ss.daily_series())
    weekly = {s: sw.weekly(b) for s, b in daily.items()}
    months = {(x.year, x.month) for b in daily.values() for x in (y[0] for y in b)}
    fast = lab.fast_months({s: [(x, o, h, lo, c) for x, o, h, lo, c, _ in b] for s, b in daily.items()}, 30)
    passes = 0
    for gname, uni in (("ALL", {m: set(daily) for m in months}), ("BIG", {m: set(lab.BIGCAP) for m in months}), ("FAST", fast)):
        res = run(weekly, uni)
        print(f"\n== weekly {gname} ==")
        sc = {k: sw.score(v) for k, v in res.items()}
        for k, ((n1, e1, net1, w1, t1), (n2, e2, net2, w2, t2)) in sc.items():
            print(f"   {k:28} | 2016-20: {n1:5} excess {e1:+.2f}% net {net1:+.2f}% win {w1:3.0f}% t {t1:5.1f} | "
                  f"2021-26: {n2:5} excess {e2:+.2f}% net {net2:+.2f}% win {w2:3.0f}% t {t2:5.1f}", flush=True)
        g, p = sc["GEMMA A (connors + uptrend)"], sc["plain weekly connors (#94)"]
        ok = g[0][1] > 0 and g[0][4] >= 3 and g[1][1] > 0 and g[1][4] >= 3 and g[0][1] > p[0][1] and g[1][1] > p[1][1]
        passes += ok
        print(f"   GEMMA A: {'PASS' if ok else 'FAIL'}")
    print(f"\nPASS: {passes} of 3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
