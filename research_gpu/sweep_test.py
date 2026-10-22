"""TRIALS #85 and #86: the ITI liquidity sweep (signals/sweep.py) on history,
then the sweep entry with dollar exits ("try both after that together").

Operator, 2026-10-22: "test this one" and "Yes, test it. and try botha after that together".

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROWS, BEFORE THE FIRST RUN.
  data       15m: data/intraday_alpaca (501 S&P, 2024-09 -> 2026-09); 5m:
             data/research_5m (107 names, 2024-10 -> 2026-10, regular hours)
  groups     BIG (signal_lab.BIGCAP, every month) and FAST (monthly fastest,
             point in time, signal_lab.fast_months: 30 for 15m, 20 for 5m)
  signal     sweep.detect's rules on candle i (computed with running values,
             same definitions): before 15:30; low < level < close for the prior
             session's low or the lowest low of the 20 candles before; volume >=
             2 x their average
  entry      the OPEN of candle i+1 (the first price after the signal closes)
  #85 exits  the article (sweep.manage): stop = low[i] - 0.1 x ATR(14);
             target 1 = session VWAP at i if above the entry, else +1R (half,
             then the stop goes to the entry); target 2 = the 20-candle high if
             above target 1, else +2R; out by the 15:55 candle's close.
             "+scalp": everything at target 1. Within a candle: stop first.
             CONTROL: for every sweep trade, one RANDOM entry on the same name
             and day (seeded), a candle before 15:30 with no signal, same risk %
             and the same target rules from that candle.
             PASS (each of 8: 2 timeframes x 2 groups x full/scalp): mean net a
             trade > 0 with t >= 2 over days in BOTH halves (split 2025-09-01),
             AND above its random control in both halves.
  #86 exits  the sweep entry with TRIALS #84's 12 dollar pairs (target +0.25 /
             0.5 / 1 / 2 %, stop -0.5 / 1 / 2 %, $2,000 lot), out by 15:55.
             SELECT per timeframe x group on the first half (highest t, >= 100
             trades); PASS: second half mean > 0, t >= 2.
  costs      0.05% a side (rules.COST). One trade at a time per name and version.
READ-ONLY research.
"""

from __future__ import annotations

import math
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, time
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import signal_lab as lab  # noqa: E402

from scanner.signals import indicators as ind  # noqa: E402
from scanner.signals import rules, sweep  # noqa: E402

SPLIT = date(2025, 9, 1)
LAST_ENTRY, LAST_EXIT = sweep.LAST_ENTRY, sweep.LAST_EXIT
TARGETS, STOPS = (0.25, 0.5, 1.0, 2.0), (0.5, 1.0, 2.0)
N = sweep.LOOKBACK


def prep(b):
    t = [x[0] for x in b]
    o, h, lo, c, v = ([x[k] for x in b] for k in range(1, 6))
    atr = ind.atr(h, lo, c, 14)
    vw, prev_low = [], []
    day_low: dict[date, float] = {}
    for d, x in zip(t, lo):
        day_low[d.date()] = min(day_low.get(d.date(), x), x)
    days = sorted(day_low)
    before = {d: (day_low[days[k - 1]] if k else None) for k, d in enumerate(days)}
    pv = vol = 0.0
    for i in range(len(t)):
        if i == 0 or t[i].date() != t[i - 1].date():
            pv = vol = 0.0
        pv += (h[i] + lo[i] + c[i]) / 3 * v[i]
        vol += v[i]
        vw.append(pv / vol if vol else None)
        prev_low.append(before[t[i].date()])
    return t, o, h, lo, c, v, atr, vw, prev_low


def signals(t, h, lo, c, v, prev_low) -> list[int]:
    out = []
    for i in range(N + 1, len(c) - 1):
        if t[i].time() >= LAST_ENTRY or t[i + 1].date() != t[i].date():
            continue
        swing = min(lo[i - N:i])
        swept = [x for x in (swing, prev_low[i]) if x is not None and lo[i] < x < c[i]]
        avg = sum(v[i - N:i]) / N
        if swept and avg and v[i] >= sweep.VOL_SURGE * avg:
            out.append(i)
    return out


def run_exit(t, o, h, lo, c, e, stop, t1, t2, mode):
    """Net % of one trade entered at the open of candle e. mode: full / scalp / fixed."""
    entry = o[e]
    cost = lambda px: (px * (1 - rules.COST)) / (entry * (1 + rules.COST)) - 1  # noqa: E731
    got, share, half = 0.0, 1.0, False
    j = e
    while j < len(c) and t[j].date() == t[e].date():
        if lo[j] <= stop:
            return (got + share * cost(min(o[j], stop) if j > e else stop)) * 100, j
        if mode == "fixed":
            if h[j] > t1:
                return cost(t1) * 100, j
        elif not half and h[j] >= t1:
            if mode == "scalp":
                return cost(t1) * 100, j
            got += 0.5 * cost(t1)
            half, share, stop = True, 0.5, entry
        if half and h[j] >= t2:
            return (got + 0.5 * cost(t2)) * 100, j
        if t[j].time() >= LAST_EXIT:
            break
        j += 1
    j = min(j, len(c) - 1)
    return (got + share * cost(c[j])) * 100, j


def plan(o, h, lo, atr, vw, i, e, risk_pct=None):
    entry = o[e]
    stop = lo[i] - 0.1 * (atr[i] or 0.0) if risk_pct is None else entry * (1 - risk_pct)
    risk = entry - stop
    if risk <= 0:
        return None
    t1 = vw[i] if vw[i] is not None and vw[i] > entry else entry + risk
    hi = max(h[i - N:i])
    t2 = hi if hi > t1 else entry + 2 * risk
    return stop, t1, t2


def tstat(rows):
    by_day = defaultdict(list)
    for d, x in rows:
        by_day[d].append(x)
    days = [statistics.mean(v) for v in by_day.values()]
    if len(days) < 3 or not statistics.stdev(days):
        return float("nan")
    return statistics.mean(days) / statistics.stdev(days) * math.sqrt(len(days))


def mean(rows):
    return statistics.mean(x for _, x in rows) if rows else float("nan")


def halves(rows):
    return [r for r in rows if r[0] < SPLIT], [r for r in rows if r[0] >= SPLIT]


def main() -> int:
    s15 = {p.name.split("__")[0]: lab.load_15m(p) for p in sorted((ROOT / "data/intraday_alpaca").glob("*__15m__session.json"))}
    stores = {"15m": s15, "5m": {p.stem: lab.load_5m(p) for p in sorted((ROOT / "data/research_5m").glob("*.json"))}}
    p85 = p86 = 0
    for tf, series in stores.items():
        months = {(d.year, d.month) for b in series.values() for d in (x[0] for x in b)}
        fast = lab.fast_months({s: lab.daily(b) for s, b in series.items()}, lab.FAST_N[tf])
        groups = {"BIG": {m: set(lab.BIGCAP) for m in months}, "FAST": fast}
        for gname, uni in groups.items():
            names = set().union(*uni.values()) & set(series)
            res: dict[str, list] = defaultdict(list)
            for s in sorted(names):
                t, o, h, lo, c, v, atr, vw, prev_low = prep(series[s])
                if len(c) < 300:
                    continue
                rnd = random.Random(f"{s}{tf}")
                sig = [i for i in signals(t, h, lo, c, v, prev_low) if s in uni.get((t[i].year, t[i].month), ())]
                sig_set = set(sig)
                by_day = defaultdict(list)
                for k, d in enumerate(t):
                    if N < k < len(c) - 1 and d.time() < LAST_ENTRY and k not in sig_set:
                        by_day[d.date()].append(k)
                versions = {"full": "full", "scalp": "scalp", **{f"fix {tp}/{sp}": (tp, sp) for tp, sp in product(TARGETS, STOPS)}}
                for name, mode in versions.items():
                    busy = -1
                    for i in sig:
                        e = i + 1
                        if e <= busy:
                            continue
                        if isinstance(mode, tuple):
                            entry = o[e]
                            net, j = run_exit(t, o, h, lo, c, e, entry * (1 - mode[1] / 100),
                                              entry * (1 + mode[0] / 100), None, "fixed")
                        else:
                            pl = plan(o, h, lo, atr, vw, i, e)
                            if pl is None:
                                continue
                            net, j = run_exit(t, o, h, lo, c, e, *pl, mode)
                            if mode == "full":
                                cands = by_day.get(t[i].date(), [])
                                if cands:
                                    r = rnd.choice(cands)
                                    rp = plan(o, h, lo, atr, vw, r, r + 1, risk_pct=(o[e] - pl[0]) / o[e])
                                    if rp is not None:
                                        cn, _ = run_exit(t, o, h, lo, c, r + 1, *rp, "full")
                                        res["control"].append((t[i].date(), cn))
                        res[name].append((t[e].date(), net))
                        busy = j
            print(f"\n== {tf} {gname} ({len(names)} names) ==")
            ctl = halves(res["control"])
            for name in ("full", "scalp"):
                a, b = halves(res[name])
                ok = (mean(a) > 0 and tstat(a) >= 2 and mean(b) > 0 and tstat(b) >= 2
                      and mean(a) > mean(ctl[0]) and mean(b) > mean(ctl[1]))
                p85 += ok
                print(f"   #85 sweep {name:5}: H1 {len(a):5} trades {mean(a):+.3f}% t {tstat(a):5.1f} | "
                      f"H2 {len(b):5} {mean(b):+.3f}% t {tstat(b):5.1f} | random control H1 {mean(ctl[0]):+.3f}% "
                      f"H2 {mean(ctl[1]):+.3f}% | {'PASS' if ok else 'FAIL'}")
            fixed = {k: halves(v) for k, v in res.items() if k.startswith("fix")}
            ok_k = [k for k, (a, _) in fixed.items() if len(a) >= 100]
            for k in sorted(fixed, key=lambda k: -(tstat(fixed[k][0]) if tstat(fixed[k][0]) == tstat(fixed[k][0]) else -99))[:4]:
                a, b = fixed[k]
                tp, sp = (float(x) for x in k.split()[1].split("/"))
                print(f"   #86 sweep + ${tp * 20:.0f}/-${sp * 20:.0f}: H1 {len(a):5} {mean(a):+.3f}% t {tstat(a):5.1f} | "
                      f"H2 {len(b):5} {mean(b):+.3f}% t {tstat(b):5.1f}")
            if ok_k:
                best = max(ok_k, key=lambda k: tstat(fixed[k][0]) if tstat(fixed[k][0]) == tstat(fixed[k][0]) else -99)
                b = fixed[best][1]
                ok = mean(b) > 0 and tstat(b) >= 2
                p86 += ok
                print(f"   #86 SELECTED on H1: {best} -> H2 {mean(b):+.3f}% t {tstat(b):.1f}: {'PASS' if ok else 'FAIL'}")
    print(f"\n#85 PASS: {p85} of 8    #86 PASS: {p86} of 4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
