"""TRIALS #94: the operator's 11 Pine signals on DAILY and WEEKLY candles, corrected scoring.

Operator, 2026-10-22: "since you test was wrong. lets run it again on a bigger time frame".
#80/#81 ran these on 5m / 15m / 1h, where 0.10% costs swamp short moves. On daily and
weekly candles a trade aims for a much larger move for the same cost.

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  data       Yahoo daily, 10 years, today's S&P 500 members; WEEKLY = daily grouped by
             ISO week (open of the first day, close of the last)
  signals    scanner/signals/rules.py RULES (11; PSAR is Pine-exact, all checked
             against PineTS), each plain and "+adx" (entry only when ADX(14) > 25)
  trades     rules.simulate with friday_flat=False (daily/weekly positions hold
             through weekends): next-open fills, 3 x ATR(22) stop of that timeframe,
             0.05% a side; entries only in months the name is in its group
  groups     ALL, BIG (signal_lab.BIGCAP), FAST (monthly fastest 30, point in time)
  measure    EXCESS = net % - the name's average return a candle x candles held;
             t over each exit day's SUM of trade results (daily P&L)
  halves     2016-10 -> 2020-12, 2021-01 -> 2026-10
  checks     per timeframe, a CHEAT signal must earn > +1% a trade and a RANDOM
             signal must lose (excess < 0) in both halves, or the run is void
  PASS       excess > 0 with t >= 3 in BOTH halves (132 versions)
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


def weekly(b):
    out: list = []
    for d, o, h, lo, c, v in b:
        k = d.isocalendar()[:2]
        if out and out[-1][0] == k:
            _, (d0, o0, h0, l0, _, v0) = out[-1]
            out[-1] = (k, (d, o0, max(h0, h), min(l0, lo), c, v0 + v))
        else:
            out.append((k, (d, o, h, lo, c, v)))
    return [x for _, x in out]


def run(series, uni):
    res = defaultdict(list)
    for s in sorted(set().union(*uni.values()) & set(series)):
        b = series[s]
        if len(b) < 120:
            continue
        d = [x[0] for x in b]
        t = [datetime(x.year, x.month, x.day, 16) for x in d]
        o, h, lo, c, v = ([x[k] for x in b] for k in range(1, 6))
        may = [s in uni.get((x.year, x.month), ()) for x in d]
        a = ind.atr(h, lo, c, 22)
        ad = ind.adx(h, lo, c)
        adx_ok = [x is not None and x > rules.ADX_MIN for x in ad]
        rets = [None] + [(c[i] / c[i - 1] - 1) * 100 for i in range(1, len(c))]
        drift = {}
        for half in (True, False):
            xs = [r for x, r in zip(d, rets) if r is not None and (x < SPLIT) == half]
            drift[half] = statistics.mean(xs) if xs else 0.0
        rnd = random.Random(s)
        states = {f"{name}{suffix}": (rule(o, h, lo, c, v), gate)
                  for name, (_, rule) in rules.RULES.items() for suffix, gate in (("", None), ("+adx", adx_ok))}
        states["CHEAT"] = (rules._machine([i + 2 < len(c) and c[i + 2] > o[i + 1] for i in range(len(c))],
                                          [i + 2 >= len(c) or c[i + 2] <= o[i + 1] for i in range(len(c))]), None)
        coin = [rnd.random() < 0.5 for _ in c]
        states["RANDOM"] = ([1 if x else 0 for x in coin], None)
        for name, (st, gate) in states.items():
            for tr in rules.simulate(s, t, o, h, lo, c, st, entry_ok=gate, may_enter=may, atr_series=a,
                                     friday_flat=False):
                day = tr.exit_at.date()
                res[name].append((day, tr.net_pct, tr.net_pct - drift[day < SPLIT] * tr.bars))
    return res


def score(rows):
    out = []
    for half in (True, False):
        r = [x for x in rows if (x[0] < SPLIT) == half]
        by = defaultdict(float)
        for x in r:
            by[x[0]] += x[2]
        days = list(by.values())
        tt = statistics.mean(days) / statistics.stdev(days) * math.sqrt(len(days)) if len(days) > 2 and statistics.stdev(days) else float("nan")
        n = len(r)
        out.append((n, statistics.mean(x[2] for x in r) if r else float("nan"),
                    statistics.mean(x[1] for x in r) if r else float("nan"),
                    sum(x[1] > 0 for x in r) / n * 100 if n else float("nan"), tt))
    return out


def main() -> int:
    daily = asyncio.run(ss.daily_series())
    stores = {"daily": daily, "weekly": {s: weekly(b) for s, b in daily.items()}}
    months = {(x.year, x.month) for b in daily.values() for x in (y[0] for y in b)}
    fast = lab.fast_months({s: [(x, o, h, lo, c) for x, o, h, lo, c, _ in b] for s, b in daily.items()}, 30)
    groups = {"ALL": {m: set(daily) for m in months}, "BIG": {m: set(lab.BIGCAP) for m in months}, "FAST": fast}
    passes, void = 0, []
    for tf, series in stores.items():
        for gname, uni in groups.items():
            res = run(series, uni)
            print(f"\n== {tf} {gname} ==")
            order = sorted((k for k in res if k not in ("CHEAT", "RANDOM")), key=lambda k: -score(res[k])[0][1])
            for k in ["CHEAT", "RANDOM", *order]:
                (n1, e1, net1, w1, t1), (n2, e2, net2, w2, t2) = score(res[k])
                ok = k not in ("CHEAT", "RANDOM") and e1 > 0 and t1 >= 3 and e2 > 0 and t2 >= 3
                passes += ok
                print(f"   {k:16} | 2016-20: {n1:6} excess {e1:+.2f}% net {net1:+.2f}% win {w1:3.0f}% t {t1:5.1f} | "
                      f"2021-26: {n2:6} excess {e2:+.2f}% net {net2:+.2f}% win {w2:3.0f}% t {t2:5.1f} | {'PASS' if ok else ''}", flush=True)
                if k == "CHEAT" and not (e1 > 1 and e2 > 1):
                    void.append(f"{tf} {gname} cheat")
                if k == "RANDOM" and not (e1 < 0 and e2 < 0):
                    void.append(f"{tf} {gname} random")
    print(f"\nPASS: {passes} of 132" + (f"   ** CHECKS FAILED: {void} **" if void else "   (cheat and random checks OK)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
