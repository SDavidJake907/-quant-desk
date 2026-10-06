"""The operator's signals.py, tested exactly as written, against random entries.

    <GPU python> research_gpu/signals_test.py

READ-ONLY research. The strategy file is research_gpu/external/signals_v1.py,
copied UNCHANGED from the operator's Downloads (sha256 0fc497db2f8e7033...).
It runs on pandas (the GPU interpreter has it); nothing here is GPU-bound.

WHY RANDOM ENTRIES ARE THE TEST
The file's exits (2 x ATR stop, 3 x ATR chandelier trail, Supertrend flip,
50-EMA break, rising-trendline break) make money on a rising market from ANY
entry. This repo's own momentum trigger with ATR exits lost to random entries
in 38 of 40 configurations (screens/momentum.py). So the question is whether
ITS ENTRIES add anything over a coin flip under ITS OWN exits.

RULES -- FIXED 2026-10-06, COMMITTED BEFORE THIS FILE FIRST RAN
  universe  LIVE: S&P 500 + 400, 2015-2026; CRASH: S&P 500, 1997-2016 (panels
            from scripts/factor_export.py); a trade counts only if the stock
            was an index member at its entry (latest month-end list)
  strategy  add_indicators + run_positions with the file's default Config,
            one position per stock at a time, exactly as written (its 3R
            "partial" is only flagged in the file, so it is not taken here)
  costs     10bps round trip on every trade (25bps also shown)
  control   the SAME run_positions, with the entry column replaced by random
            days at the SAME per-stock entry rate (warm-up respected), 5 seeds
  measure   per trade: % return after costs, minus the control's average
            trade in the same calendar month; monthly means, Newey-West t
PASS (both): LIVE signal-minus-random > 0 with t >= 2, AND positive on CRASH.
Also shown: win rate, average R, trades per stock-year. TRIALS.md #66.
"""

from __future__ import annotations

import importlib.util
import math
import pickle
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("signals_v1", ROOT / "external" / "signals_v1.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

COST, COST_HI, SEEDS = 0.0010, 0.0025, (1, 2, 3, 4, 5)


def nw_t(xs, lag=2):
    n = len(xs)
    if n < lag + 3:
        return 0.0
    m = sum(xs) / n
    d = [x - m for x in xs]
    var = sum(v * v for v in d) / n
    for k in range(1, lag + 1):
        var += 2 * (1 - k / (lag + 1)) * sum(d[i] * d[i - k] for i in range(k, n)) / n
    return m / math.sqrt(var / n) if var > 0 else 0.0


def frames(name):
    p = pickle.loads((ROOT.parent / "data" / "factors" / f"panel_{name}.pkl").read_bytes())
    t, n = len(p["dates"]), len(p["symbols"])
    idx = pd.to_datetime(p["dates"])
    arr = {k: np.frombuffer(p[k], dtype=np.float64).reshape(t, n) for k in ("open", "high", "low", "close", "volume")}
    ends = sorted(int(k) for k in p["members"])
    members = {int(k): set(v) for k, v in p["members"].items()}
    for j, sym in enumerate(p["symbols"]):
        df = pd.DataFrame({k: arr[k][:, j] for k in arr}, index=idx).dropna()
        if len(df) > S.Config.ema_slow + 50:
            yield sym, j, df, ends, members, p["dates"]


def member_on(day_index, ends, members, j):
    k = max((e for e in ends if e <= day_index), default=None)
    return k is not None and j in members[k]


def trades_of(df, entry_col=None):
    x = df if entry_col is None else df.assign(entry=entry_col,
                                               entry_breakout=entry_col, entry_pullback=False)
    _, tr = S.run_positions(x)
    return tr


def run(name):
    t0 = time.time()
    sig_by_month, rnd_by_month = defaultdict(list), defaultdict(list)
    sig_all, sig_r, stock_years = [], [], 0.0
    for sym, j, raw, ends, members, dates in frames(name):
        df = S.add_indicators(raw)
        pos = {d: i for i, d in enumerate(pd.to_datetime(dates))}
        tr = trades_of(df)
        stock_years += len(df) / 252
        rate = df["entry"].mean()
        warm = np.arange(len(df)) < S.Config.ema_slow
        for _, t in tr.iterrows():
            if not member_on(pos[t.entry_time], ends, members, j):
                continue
            ret = t.ret_pct / 100 - COST
            sig_all.append(ret)
            sig_r.append(t.r_mult)
            sig_by_month[t.entry_time.strftime("%Y-%m")].append(ret)
        for seed in SEEDS:
            rng = np.random.default_rng(seed * 7919 + j)
            fake = pd.Series((rng.random(len(df)) < rate) & ~warm, index=df.index)
            for _, t in trades_of(df, fake).iterrows():
                if member_on(pos[t.entry_time], ends, members, j):
                    rnd_by_month[t.entry_time.strftime("%Y-%m")].append(t.ret_pct / 100 - COST)
    months = sorted(m for m in sig_by_month if rnd_by_month.get(m))
    gap = [statistics.fmean(sig_by_month[m]) - statistics.fmean(rnd_by_month[m]) for m in months]
    rnd_all = [x for m in rnd_by_month for x in rnd_by_month[m]]
    out = {"n": len(sig_all), "mean": statistics.fmean(sig_all), "rand": statistics.fmean(rnd_all),
           "gap": statistics.fmean(gap), "t": nw_t(gap), "win": sum(x > 0 for x in sig_all) / len(sig_all),
           "avg_r": statistics.fmean(sig_r), "per_stock_year": len(sig_all) / stock_years,
           "mean_hi": statistics.fmean(sig_all) - (COST_HI - COST)}
    print(f"\n{name}: {out['n']} trades ({out['per_stock_year']:.1f} per stock-year), {time.time() - t0:.0f}s")
    print(f"   signals   {out['mean'] * 100:+.2f}%/trade after 10bps ({out['mean_hi'] * 100:+.2f}% at 25bps), "
          f"win {out['win'] * 100:.0f}%, avg R {out['avg_r']:+.2f}")
    print(f"   random    {out['rand'] * 100:+.2f}%/trade, same exits, same entry rate, 5 seeds")
    print(f"   signals minus random, per month: {out['gap'] * 100:+.2f}pp  NW t {out['t']:.2f}  ({len(months)} months)")
    return out


def main() -> int:
    live, crash = run("LIVE"), run("CRASH")
    ok = live["gap"] > 0 and live["t"] >= 2 and crash["gap"] > 0
    print(f"\nPASS = LIVE signals-minus-random > 0 with t >= 2 AND positive on CRASH -> {'PASS' if ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
