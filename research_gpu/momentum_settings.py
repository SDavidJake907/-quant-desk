"""TRIALS #76: which momentum SETTINGS hold up -- lookback, how many names, sector cap.

Operator, 2026-10-19: "lets look for the correct setting for the momemtum, cause
this doesnt seem to be working". The dry-run tranche formed 2026-10-06 (8 of 10
names Information Technology) is -17.6% vs SPY -1.6% two weeks later.

Engine (as live, momentum/signal.py): rank members by momentum, top N, a new
book every 21 trading days held 63 days (three overlapping tranches), CASH when
the S&P 500's prior close is below its 210-day average. Entry next open, exit
close 63 days later, 25 bps per hold. Random control: N random members, same
filter, seeds 1-5.

GRID (12 cells, all published variants, none tuned here):
  lookback  12-1 (live; Jegadeesh-Titman)  |  6-1
  top N     10 (live)  |  20  |  30
  sector    no cap (live)  |  max 3 per GICS sector (sectors as of 2026-10;
            names with no sector, mostly delisted CRASH names, are uncapped)

Panels: data/factors/panel_LIVE.pkl (S&P 500+400, 2014-26) and panel_CRASH.pkl
(S&P 500, 1997-2016); point-in-time members; survivor-biased (see the panels).

CHANGE RULE, fixed now: a cell replaces the live setting only if, on BOTH
panels, (a) momentum minus random per hold >= the live cell's, (b) max drawdown
<= the live cell's, and (c) LIVE Newey-West t >= 1.5. If no cell qualifies the
live setting stays. A cell that wins one panel and loses the other does not.
"""

from __future__ import annotations

import json
import math
import pickle
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
HOLD, STEP, COST = 63, 21, 0.0025
SMA = 210
LOOKBACKS = {"12-1": 252, "6-1": 126}
SKIP = 21
TOPS = (10, 20, 30)
CAPS = (None, 3)
SEEDS = (1, 2, 3, 4, 5)
LIVE_CELL = ("12-1", 10, None)


def nw_t(xs, lag=2):
    n = len(xs)
    m = sum(xs) / n
    d = [x - m for x in xs]
    var = sum(v * v for v in d) / n
    for k in range(1, lag + 1):
        var += 2 * (1 - k / (lag + 1)) * sum(d[i] * d[i - k] for i in range(k, n)) / n
    return m / math.sqrt(var / n) if var > 0 else 0.0


def load(name):
    p = pickle.loads((ROOT / "data" / "factors" / f"panel_{name}.pkl").read_bytes())
    t, n = len(p["dates"]), len(p["symbols"])
    o = np.frombuffer(p["open"], dtype=np.float64).reshape(t, n)
    c = np.frombuffer(p["close"], dtype=np.float64).reshape(t, n)
    bench = np.frombuffer(p["bench"], dtype=np.float64)
    ends = sorted(int(k) for k in p["members"])
    members = {int(k): set(v) for k, v in p["members"].items()}
    return p, o, c, bench, ends, members


def members_at(d, ends, members):
    k = max((e for e in ends if e <= d), default=None)
    return members.get(k, set()) if k is not None else set()


def book_return(o, c, d, picks):
    """Mean return, next open -> close HOLD days on (last valid close if it ends early)."""
    rs = []
    last = min(d + HOLD, c.shape[0] - 1)
    for j in picks:
        entry = o[d + 1, j]
        seg = c[d + 1:last + 1, j]
        seg = seg[~np.isnan(seg)]
        if not np.isfinite(entry) or entry <= 0 or not len(seg):
            continue
        rs.append(seg[-1] / entry - 1)
    return float(np.mean(rs)) - COST if rs else 0.0


def pick(scores, top, cap, sectors, symbols):
    out, count = [], {}
    for j in sorted(scores, key=lambda j: (-scores[j], symbols[j])):
        sec = sectors.get(symbols[j])
        if cap and sec and count.get(sec, 0) >= cap:
            continue
        out.append(j)
        if sec:
            count[sec] = count.get(sec, 0) + 1
        if len(out) == top:
            break
    return out


def account(series):
    eq, peak, dd = [1 / 3] * 3, 1.0, 0.0
    for i, r in enumerate(series):
        eq[i % 3] *= 1 + r
        tot = sum(eq)
        peak = max(peak, tot)
        dd = max(dd, (peak - tot) / peak)
    return sum(eq) - 1, dd


def run(name, sectors):
    p, o, c, bench, ends, members = load(name)
    symbols, dates = p["symbols"], p["dates"]
    T = c.shape[0]
    days = list(range(max(LOOKBACKS.values()) + 5, T - HOLD - 1, STEP))
    years = (len(days) * STEP) / 252
    res = {}
    rand = {top: [] for top in TOPS}
    invested = []
    for d in days:
        on = d >= SMA and bench[d - 1] > np.nanmean(bench[d - SMA:d])
        invested.append(on)
    for top in TOPS:
        for d, on in zip(days, invested):
            mem = [j for j in members_at(d, ends, members) if np.isfinite(c[d, j])]
            vals = []
            for s in SEEDS:
                rng = random.Random(s * 100000 + d)
                vals.append(book_return(o, c, d, rng.sample(mem, min(top, len(mem)))) if on else 0.0)
            rand[top].append(sum(vals) / len(vals))
    for lb_name, lb in LOOKBACKS.items():
        for top in TOPS:
            for cap in CAPS:
                series, gaps = [], []
                for k, (d, on) in enumerate(zip(days, invested)):
                    if not on:
                        series.append(0.0); gaps.append(0.0)
                        continue
                    mem = members_at(d, ends, members)
                    a, b = c[d - SKIP], c[d - lb]
                    scores = {j: a[j] / b[j] - 1 for j in mem
                              if np.isfinite(a[j]) and np.isfinite(b[j]) and b[j] > 0 and np.isfinite(c[d, j])}
                    r = book_return(o, c, d, pick(scores, top, cap, sectors, symbols))
                    series.append(r); gaps.append(r - rand[top][k])
                total, dd = account(series)
                res[(lb_name, top, cap)] = {
                    "hold": float(np.mean(series)), "gap": float(np.mean(gaps)), "t": nw_t(gaps),
                    "cagr": (1 + total) ** (1 / years) - 1, "dd": dd, "worst": min(series)}
    print(f"\n{name}: {dates[days[0]]} .. {dates[days[-1]]}, {len(days)} books, "
          f"invested {sum(invested) / len(invested) * 100:.0f}% of books")
    print(f"  {'lookback':<8} {'top':>3} {'cap':>4} | {'per hold':>8} {'vs random':>9} {'NW t':>6} "
          f"{'CAGR':>7} {'max DD':>7} {'worst book':>10}")
    for (lb_name, top, cap), r in res.items():
        tag = "  <- LIVE" if (lb_name, top, cap) == LIVE_CELL else ""
        print(f"  {lb_name:<8} {top:>3} {str(cap or '-'):>4} | {r['hold']*100:+7.2f}% {r['gap']*100:+8.2f}pp "
              f"{r['t']:+6.2f} {r['cagr']*100:+6.1f}% {r['dd']*100:6.1f}% {r['worst']*100:+9.1f}%{tag}")
    return res


def main() -> int:
    sectors = json.loads((ROOT / "data" / "factors" / "sectors_now.json").read_text())
    out = {n: run(n, sectors) for n in ("LIVE", "CRASH")}
    live = {n: out[n][LIVE_CELL] for n in out}
    print("\nCHANGE RULE: beats the live cell on BOTH panels (gap >= live, DD <= live) and LIVE t >= 1.5")
    winners = []
    for cell in out["LIVE"]:
        if cell == LIVE_CELL:
            continue
        ok = all(out[n][cell]["gap"] >= live[n]["gap"] and out[n][cell]["dd"] <= live[n]["dd"]
                 for n in out) and out["LIVE"][cell]["t"] >= 1.5
        if ok:
            winners.append(cell)
    print("  qualifies:", winners or "none -- the live setting stays")
    return 0


if __name__ == "__main__":
    sys.exit(main())
