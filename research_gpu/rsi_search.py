"""TRIALS #82: the best RSI buy/sell levels, found WALK-FORWARD.

Operator, 2026-10-22: "lets find the right RSI when its the highest, and the
lowest using the best indicators to edit the pine scripts to find the best signals".

Searching settings on history always finds a winner by luck, so the search
only ever sees YEAR 1 and the winner is judged on YEAR 2, which it never saw.

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  rule        RSI mean reversion, long only: buy when RSI(length) closes below
              BUY, sell when it closes above SELL (scanner/signals/indicators.rsi,
              Pine's ta.rsi); trades by rules.simulate exactly as #80/#81
              (next-open fills, 3 x ATR(22) stop, flat Friday, 0.05% a side)
  grid        length {2, 3, 5, 14} x BUY {5, 10, 15, 20, 30} x SELL {50, 70, 90}
              x filter {none, "trend": close above EMA(200) of the same candles,
              the best-known RSI companion (Connors)} = 120 settings
  where       {fast movers (as #80), big caps (as #81)} x {15m, 1h} = 4 searches
  measure     excess over the name's drift, averaged per exit day, t over days
  SELECT      in YEAR 1 (to 2025-08-31) only: the setting with the highest t
              among those with >= 100 trades, per search
  PASS        that ONE setting, in YEAR 2 (from 2025-09-01): excess > 0, t >= 2.
              Four searches, so one pass by luck is ~1 in 10; two or more mean
              something.
READ-ONLY research. Changes nothing live.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import signal_lab as lab  # noqa: E402

from scanner.signals import indicators as ind  # noqa: E402
from scanner.signals import rules  # noqa: E402

LENGTHS, BUYS, SELLS, FILTERS = (2, 3, 5, 14), (5, 10, 15, 20, 30), (50, 70, 90), ("none", "trend")
MIN_TRADES = 100


def name(length, buy, sell, filt):
    return f"RSI({length}) buy<{buy} sell>{sell}" + (" +trend" if filt == "trend" else "")


def search(series: dict, universe_by_month: dict) -> dict[str, list]:
    res: dict[str, list] = defaultdict(list)
    names = set().union(*universe_by_month.values()) if universe_by_month else set()
    for s in sorted(names):
        b = series.get(s)
        if not b or len(b) < 300:
            continue
        t = [x[0] for x in b]
        o, h, lo, c, v = ([x[k] for x in b] for k in range(1, 6))
        may = [s in universe_by_month.get((d.year, d.month), ()) for d in t]
        a = ind.atr(h, lo, c, 22)
        e200 = ind.ema(c, 200)
        trend_ok = [x is not None and px > x for px, x in zip(c, e200)]
        rets = [None] + [(c[i] / c[i - 1] - 1) * 100 for i in range(1, len(c))]
        drift = {}
        for half in (True, False):
            xs = [r for d, r in zip(t, rets) if r is not None and (d.date() < lab.SPLIT) == half]
            drift[half] = sum(xs) / len(xs) if xs else 0.0
        for length in LENGTHS:
            r = ind.rsi(c, length)
            for buy, sell in product(BUYS, SELLS):
                state = rules._machine([x is not None and x < buy for x in r],
                                       [x is not None and x > sell for x in r])
                for filt in FILTERS:
                    for tr in rules.simulate(s, t, o, h, lo, c, state, may_enter=may, atr_series=a,
                                             entry_ok=trend_ok if filt == "trend" else None):
                        d = tr.exit_at.date()
                        res[name(length, buy, sell, filt)].append(
                            (d, tr.net_pct, tr.net_pct - drift[d < lab.SPLIT] * tr.bars, tr.bars))
    return res


def main() -> int:
    s15 = {p.name.split("__")[0]: lab.load_15m(p) for p in sorted((ROOT / "data/intraday_alpaca").glob("*__15m__session.json"))}
    stores = {"15m": s15, "1h": {s: lab.to_hourly(b) for s, b in s15.items()}}
    passes = 0
    for (tf, series), where in product(stores.items(), ("fast", "big")):
        if where == "fast":
            uni = lab.fast_months({s: lab.daily(b) for s, b in series.items()}, lab.FAST_N[tf])
        else:
            months = {(d.year, d.month) for b in series.values() for d in (x[0] for x in b)}
            uni = {m: set(lab.BIGCAP) for m in months}
        res = search(series, uni)
        y1 = {k: lab.summarize(v, True) for k, v in res.items()}
        ranked = sorted((k for k in y1 if y1[k][0] >= MIN_TRADES), key=lambda k: -(y1[k][2] if y1[k][2] == y1[k][2] else -99))
        print(f"\n== {where} movers, {tf}: {len(res)} settings searched in YEAR 1 ==")
        print(f"   {'setting':34} | Y1 n  excess     t   win | Y2 n  excess     t   win")
        for k in ranked[:5]:
            a, b = y1[k], lab.summarize(res[k], False)
            print(f"   {k:34} | {a[0]:4} {a[1]:+6.2f}% {a[2]:5.1f} {a[4]:3.0f}% | {b[0]:4} {b[1]:+6.2f}% {b[2]:5.1f} {b[4]:3.0f}%")
        if ranked:
            best = ranked[0]
            b = lab.summarize(res[best], False)
            ok = b[1] > 0 and b[2] >= 2
            passes += ok
            print(f"   SELECTED on year 1: {best} -> YEAR 2 excess {b[1]:+.2f}%, t {b[2]:.1f}: {'PASS' if ok else 'FAIL'}")
    print(f"\nPASS: {passes} of 4 searches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
