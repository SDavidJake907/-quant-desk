"""TRIALS #97 (Gemma's C, the overnight window) and #98 (Gemma's B, the sweep in an uptrend) on 24/5 candles.

Operator, 2026-10-22: "we need to fix the trading time, its open 24/5 thats why im trying
to find a stragtie". Gemma proposed both in her review; C is corrected to sell in the
MORNING (the overnight gain is close -> open; holding to the next close adds the day's noise).

DATA  pre/regular/after-hours 04:00-20:00 ET: Alpaca SIP 30m (data/intraday_ext_2024,
      collect_alpaca --extended); overnight 20:00-04:00 ET: Alpaca BOATS 30m
      (data/intraday_boats, from 2024-09-16). Merged into one 24/5 series per name.
      Halves: 2024-09-16 -> 2025-09-15 and 2025-09-16 -> now.
COSTS 0.05% a side at the 16:00 close or the 9:30 open; 0.10% a side for a fill in the
      pre-market, after-hours or overnight session (wider spreads).

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROWS, BEFORE THE FIRST RUN.
#97  every Mon-Thu night (no weekend: there is no Friday-night session), every name in the
     group: BUY at T_in, SELL the next weekday at T_out --
       T_in  in {16:00 close (the 15:30 bar's close), 16:30 (that bar's open), 20:00 (open)}
       T_out in {04:00, 08:00, 09:30} (that bar's open)
     a missing bar uses the next bar within 2 hours, else the name sits out that night.
     A night's result = the equal-weight average over the group's names (one book).
     Groups: FAST_UP (each day, the 30 fastest names by 20-day ATR % whose close is
     above their 50-day average -- the Lotto trials rule, from the prior day's data),
     ALL (every name collected: BIG + every monthly fast mover since 2024-09 + 60 S&P 500
     names drawn with seed 97 -- research_gpu/collect_ext_subset.py), BIG (signal_lab.BIGCAP).
     SELECT per group on the first half: the window with the highest t of nightly
     results; PASS: that window in the second half, mean > 0 with t >= 3.
#98  hourly candles (24/5, grouped from the 30m series). Sweep as #87 (the 20-candle low
     swept, close back above, 2x volume) AND, for "UPTREND", the prior day's regular close
     above its 50-day average. Gemma's exits: target +1%, out after 1 week (120 hourly
     candles of the 24/5 series), plus the stop under the sweep wick (low - 0.1 x ATR(14)).
     Costs by the session of each fill. Versions: UPTREND sweep, plain sweep, FAIR random
     (3 random hours per UPTREND trade, same name, any eligible hour, same exits).
     Groups FAST (monthly fastest 30) and ALL. t over daily P&L totals.
     PASS: UPTREND > 0 with t >= 3 in both halves AND above plain sweep and random in both.
READ-ONLY research.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from itertools import product
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import signal_lab as lab  # noqa: E402
import sweep_slow as ss  # noqa: E402

from scanner.signals import indicators as ind  # noqa: E402

ET = ZoneInfo("America/New_York")
SPLIT = date(2025, 9, 16)
START = date(2024, 9, 16)
T_IN = {"16:00 close": (time(15, 30), "close"), "16:30": (time(16, 30), "open"), "20:00": (time(20, 0), "open")}
T_OUT = {"04:00": time(4, 0), "08:00": time(8, 0), "09:30 open": time(9, 30)}
REG = (time(9, 30), time(16, 0))


def cost(t: time, field: str) -> float:
    regular = (t == time(15, 30) and field == "close") or t == time(9, 30)
    return 0.0005 if regular else 0.0010


def load_24(sym: str) -> list[tuple]:
    rows = []
    for f in (ROOT / f"data/intraday_ext_2024/{sym}__30m__session.json", ROOT / f"data/intraday_boats/{sym}__30m.json"):
        if f.exists():
            rows += json.loads(f.read_text())
    seen = {}
    for ts, o, h, lo, c, v in rows:
        seen.setdefault(ts, (datetime.fromtimestamp(ts, ET).replace(tzinfo=None), o, h, lo, c, v))
    return [seen[k] for k in sorted(seen) if seen[k][0].date() >= START]


def daily_regular(bars) -> list[tuple]:
    days: dict = {}
    for t, o, h, lo, c, v in bars:
        if REG[0] <= t.time() < REG[1]:
            d = days.get(t.date())
            days[t.date()] = (o, h, lo, c, v) if d is None else (d[0], max(d[1], h), min(d[2], lo), c, d[4] + v)
    return [(d, *x) for d, x in sorted(days.items())]


def tstat(xs):
    return statistics.mean(xs) / statistics.stdev(xs) * math.sqrt(len(xs)) if len(xs) > 2 and statistics.stdev(xs) else float("nan")


def fast_up_by_day(dailies: dict) -> dict[date, set[str]]:
    """day -> the 30 fastest names above their 50-day average, from data up to the PRIOR day."""
    stats: dict[date, list] = defaultdict(list)
    for s, rows in dailies.items():
        for i in range(51, len(rows)):
            prev = rows[i - 1]
            trs = [max(rows[k][2] - rows[k][3], abs(rows[k][2] - rows[k - 1][4]), abs(rows[k][3] - rows[k - 1][4])) for k in range(i - 20, i)]
            atrp = sum(trs) / 20 / prev[4] * 100
            above = prev[4] > sum(r[4] for r in rows[i - 50:i]) / 50
            if above:
                stats[rows[i][0]].append((atrp, s))
    return {d: {s for _, s in sorted(v, reverse=True)[:30]} for d, v in stats.items()}


def night_results(bars, t_in, field, t_out) -> dict[date, float]:
    """entry day -> net % for one name, buying at t_in that evening and selling the next weekday at t_out."""
    by_day: dict[date, dict[time, tuple]] = defaultdict(dict)
    for b in bars:
        by_day[b[0].date()][b[0].time()] = b
    days = sorted(by_day)
    out = {}
    for d, nd in zip(days, days[1:]):
        if d.weekday() > 3 or nd != d + timedelta(days=1):       # Mon-Thu, next calendar day only
            continue
        def find(day_bars, t0, fld, also=None):
            for k in range(5):
                tt = (datetime.combine(d, t0) + timedelta(minutes=30 * k)).time()
                pool = day_bars if also is None or tt >= t0 else also
                if tt in pool:
                    return pool[tt][1] if fld == "open" else pool[tt][4]
            return None
        # entry bars are on day d (16:00-20:00) or, for 20:00, on d too
        entry = find(by_day[d], t_in, field)
        exit_ = None
        for k in range(5):
            tt = (datetime.combine(nd, t_out) + timedelta(minutes=30 * k)).time()
            if tt in by_day[nd]:
                exit_ = by_day[nd][tt][1]
                break
        if entry and exit_:
            ci, co = cost(t_in, field), cost(t_out, "open")
            out[d] = ((exit_ * (1 - co)) / (entry * (1 + ci)) - 1) * 100
    return out


def run_97(bars24: dict, groups: dict) -> int:
    passes = 0
    for gname, members in groups.items():
        print(f"\n== #97 overnight window, {gname} ==")
        results = {}
        for (li, (t_in, field)), (lo_, t_out) in product(T_IN.items(), T_OUT.items()):
            per_night: dict[date, list] = defaultdict(list)
            for s, bars in bars24.items():
                for d, r in night_results(bars, t_in, field, t_out).items():
                    if s in members(d):
                        per_night[d].append(r)
            nights = {d: statistics.mean(v) for d, v in per_night.items() if v}
            a = [x for d, x in nights.items() if d < SPLIT]
            b = [x for d, x in nights.items() if d >= SPLIT]
            results[(li, lo_)] = (a, b)
            print(f"   buy {li:11} sell {lo_:10} | half 1: {len(a):3} nights {statistics.mean(a) if a else float('nan'):+.3f}% t {tstat(a):5.1f} | "
                  f"half 2: {len(b):3} nights {statistics.mean(b) if b else float('nan'):+.3f}% t {tstat(b):5.1f}", flush=True)
        best = max(results, key=lambda k: tstat(results[k][0]) if results[k][0] else -99)
        b = results[best][1]
        ok = bool(b) and statistics.mean(b) > 0 and tstat(b) >= 3
        passes += ok
        print(f"   SELECTED on half 1: buy {best[0]}, sell {best[1]} -> half 2 {statistics.mean(b):+.3f}% a night, t {tstat(b):.1f}: {'PASS' if ok else 'FAIL'}")
    return passes


def session_cost(t: datetime) -> float:
    return 0.0005 if REG[0] <= t.time() < REG[1] else 0.0010


def run_98(bars24: dict, dailies: dict, uni: dict) -> dict[str, list]:
    res: dict[str, list] = defaultdict(list)
    for s in sorted(set().union(*uni.values()) & set(bars24)):
        hrs: list = []
        for t, o, h, lo, c, v in bars24[s]:
            k = (t.date(), t.hour)
            if hrs and hrs[-1][0] == k:
                _, (t0, o0, h0, l0, _, v0) = hrs[-1]
                hrs[-1] = (k, (t0, o0, max(h0, h), min(l0, lo), c, v0 + v))
            else:
                hrs.append((k, (t, o, h, lo, c, v)))
        b = [x for _, x in hrs]
        if len(b) < 300:
            continue
        t = [x[0] for x in b]
        o, h, lo, c, v = ([x[k] for x in b] for k in range(1, 6))
        atr = ind.atr(h, lo, c, 14)
        drows = dailies.get(s, [])
        up_day = {}
        for i in range(50, len(drows)):
            up_day[drows[i][0]] = drows[i - 1][4] > sum(r[4] for r in drows[i - 50:i]) / 50
        elig = [i for i in range(21, len(c) - 1) if s in uni.get((t[i].year, t[i].month), ())]
        rnd = random.Random(f"98{s}")
        busy = {"UPTREND": -1, "PLAIN": -1}

        def trade(e, stop):
            entry = o[e]
            ce = session_cost(t[e])
            tgt = entry * 1.01
            for j in range(e, min(e + 120, len(c))):
                if lo[j] <= stop:
                    px = min(o[j], stop) if j > e else stop
                    return ((px * (1 - session_cost(t[j]))) / (entry * (1 + ce)) - 1) * 100, j
                if h[j] >= tgt:
                    px = max(o[j], tgt) if j > e else tgt
                    return ((px * (1 - session_cost(t[j]))) / (entry * (1 + ce)) - 1) * 100, j
            j = min(e + 120, len(c)) - 1
            return ((c[j] * (1 - session_cost(t[j]))) / (entry * (1 + ce)) - 1) * 100, j

        for i in elig:
            swing, avg = min(lo[i - 20:i]), sum(v[i - 20:i]) / 20
            if not (lo[i] < swing < c[i] and avg and v[i] >= 2 * avg):
                continue
            e = i + 1
            stop = lo[i] - 0.1 * (atr[i] or 0.0)
            if o[e] - stop <= 0:
                continue
            for version in ("PLAIN", "UPTREND"):
                if version == "UPTREND" and not up_day.get(t[i].date(), False):
                    continue
                if e <= busy[version]:
                    continue
                net, j = trade(e, stop)
                res[version].append((t[j].date(), net))
                busy[version] = j
                if version == "UPTREND":
                    for _ in range(3):
                        r = rnd.choice(elig) + 1
                        rs = o[r] * (1 - (o[e] - stop) / o[e])
                        cn, cj = trade(r, rs)
                        res["RANDOM"].append((t[cj].date(), cn))
    return res


def score98(rows):
    out = []
    for half in (True, False):
        r = [x for x in rows if (x[0] < SPLIT) == half]
        by = defaultdict(float)
        for d, x in r:
            by[d] += x
        out.append((len(r), statistics.mean(x for _, x in r) if r else float("nan"), tstat(list(by.values()))))
    return out


def main() -> int:
    names = sorted({p.name.split("__")[0] for p in (ROOT / "data/intraday_ext_2024").glob("*__30m__session.json")})
    bars24 = {s: load_24(s) for s in names}
    bars24 = {s: b for s, b in bars24.items() if len(b) > 500}
    dailies = {s: daily_regular(b) for s, b in bars24.items()}
    print(f"{len(bars24)} names with 24/5 candles; overnight files: {len(list((ROOT / 'data/intraday_boats').glob('*.json')))}")
    fu = fast_up_by_day(dailies)
    groups = {"FAST_UP": lambda d: fu.get(d, set()), "ALL": lambda d: bars24.keys(), "BIG": lambda d: set(lab.BIGCAP)}
    p97 = run_97(bars24, groups)
    months = {(d.year, d.month) for b in bars24.values() for d in (x[0] for x in b)}
    fast = lab.fast_months({s: [(d, o, h, lo, c) for d, o, h, lo, c, _ in rows] for s, rows in dailies.items()}, 30)
    p98 = 0
    for gname, uni in (("FAST", fast), ("ALL", {m: set(bars24) for m in months})):
        res = run_98(bars24, dailies, uni)
        print(f"\n== #98 1h sweep (24/5), {gname} ==")
        sc = {k: score98(res.get(k, [])) for k in ("UPTREND", "PLAIN", "RANDOM")}
        for k, ((n1, m1, t1), (n2, m2, t2)) in sc.items():
            print(f"   {k:8} | half 1: {n1:5} trades {m1:+.2f}% t {t1:5.1f} | half 2: {n2:5} trades {m2:+.2f}% t {t2:5.1f}")
        (u1, u2), (p1, p2), (r1, r2) = sc["UPTREND"], sc["PLAIN"], sc["RANDOM"]
        ok = (u1[1] > 0 and u1[2] >= 3 and u2[1] > 0 and u2[2] >= 3 and u1[1] > max(p1[1], r1[1]) and u2[1] > max(p2[1], r2[1]))
        p98 += ok
        print(f"   UPTREND: {'PASS' if ok else 'FAIL'}")
    print(f"\n#97 PASS: {p97} of 3    #98 PASS: {p98} of 2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
