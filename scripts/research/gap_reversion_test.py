"""Opening-gap reversion: does a swept opening low after a gap-down fill the gap?

    python scripts/gap_reversion_test.py

READ-ONLY research. Changes nothing live.

WHERE THIS CAME FROM
gemma4:12b's strategy brainstorm, 2026-10-04: "if a stock gaps significantly
at the open and then shows a liquidity sweep in the first 30-60 minutes, trade
the reversion". A different mechanism from the swing screen -- the other side
is overnight sellers and opening momentum, not trapped stops at a swing low --
so it is tested as a separate strategy, long-only.

RULES, FIXED BEFORE ANY OUTCOME WAS LOOKED AT (regular-session 30m bars)
    gap       today's first-bar open <= prior session close x (1 - 2%)
    sweep     bar 2's low < bar 1's low AND bar 2 closes above bar 1's low
    entry     bar 2's close (10:30 ET)
    stop      the session low through bar 2
    target    the prior close (the gap filled); reward:risk >= 1 required
    exit      target, stop, or the session's last bar close -- never overnight
    control   EVERY qualifying gap-down, entered at bar 2's close with the same
              stop and target, sweep or not
    tradeable price >= $5, 20-bar mean dollar volume >= $20M per bar
Costs 10bps round trip, charged in R (10bps / risk%). Gap bands reported
too: 2-4% and >= 4%.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.execution.policy import TradingPolicy
from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED

from relative_strength_test import permutation_p

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
GAP, COST = 0.02, 0.0010


def sessions(bars):
    """Index ranges per session date, in order."""
    out: dict = defaultdict(list)
    for i, t in enumerate(bars.times):
        out[t.date()].append(i)
    return [out[d] for d in sorted(out)]


def trade(bars, day: list[int], entry_i: int, prior_close: float):
    """R net of costs for a long from bar entry_i's close, or None if untradeable."""
    entry = bars.closes[entry_i]
    stop = min(bars.lows[i] for i in day if i <= entry_i)
    target = prior_close
    risk = entry - stop
    if risk <= 0 or target <= entry or (target - entry) / risk < 1.0:
        return None
    exit_price = bars.closes[day[-1]]
    for i in day:
        if i <= entry_i:
            continue
        o, h, lo = bars.opens[i], bars.highs[i], bars.lows[i]
        if o <= stop or o >= target:
            exit_price = o
            break
        if lo <= stop:
            exit_price = stop
            break
        if h >= target:
            exit_price = target
            break
    return (exit_price - entry) / risk - COST * entry / risk


def liquid(bars, upto: int) -> bool:
    lo = max(0, upto - 20)
    window = range(lo, upto)
    if not window:
        return False
    volume = statistics.fmean(bars.volumes[i] * bars.closes[i] for i in window)
    return bars.closes[upto] >= V.min_price and volume >= V.min_dollar_volume


def show(label: str, values: list[float]) -> str:
    if not values:
        return f"{label} n=0"
    mean = statistics.fmean(values)
    tint = GREEN if mean > 0 else RED
    return (f"{label} {tint}{mean:+.3f}R{RESET} n={len(values):<4} "
            f"win {sum(v > 0 for v in values) / len(values) * 100:.0f}%")


def main(store: str, holdout: float, draws: int) -> int:
    path = Path(store)
    history.STORE_DIR = path
    sweeps, controls = [], []           # (time, gap_pct, R)
    for symbol in sorted({f.name.split("__")[0] for f in path.glob("*.json")}):
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        days = sessions(bars)
        for prev, day in zip(days, days[1:]):
            if len(day) < 4:
                continue
            prior_close = bars.closes[prev[-1]]
            b1, b2 = day[0], day[1]
            gap = bars.opens[b1] / prior_close - 1
            if gap > -GAP or not liquid(bars, b1):
                continue
            r = trade(bars, day, b2, prior_close)
            if r is None:
                continue
            row = (bars.times[b2], -gap * 100, r)
            controls.append(row)
            if bars.lows[b2] < bars.lows[b1] and bars.closes[b2] > bars.lows[b1]:
                sweeps.append(row)
    for rows in (sweeps, controls):
        rows.sort(key=lambda r: r[0])
    if len(sweeps) < 40:
        print(f"{YELLOW}only {len(sweeps)} sweep trades{RESET}")
        return 0
    cut_time = controls[int(len(controls) * (1 - holdout))][0]
    print(f"\n{len(controls)} tradeable gap-downs >= {GAP * 100:.0f}%, {len(sweeps)} with the "
          f"swept opening low; 10bps costs in R; holdout from {cut_time:%Y-%m-%d}")

    def split(rows):
        return ([r for t, _, r in rows if t < cut_time], [r for t, _, r in rows if t >= cut_time])

    for label, rows in (("SWEEP (the idea)", sweeps), ("ALL GAP-DOWNS (control)", controls)):
        train, hold = split(rows)
        print(f"\n  {BOLD}{label}{RESET}")
        print(f"    {show('train', train)}    {show('holdout', hold)}")
        for name, lo, hi in (("gap 2-4%", 2, 4), ("gap >=4%", 4, 999)):
            tr, ho = split([x for x in rows if lo <= x[1] < hi])
            print(f"      {name:<10}{show('train', tr)}    {show('holdout', ho)}")

    s_train, s_hold = split(sweeps)
    keys = {(t, g) for t, g, _ in sweeps}
    rest = [x for x in controls if (x[0], x[1]) not in keys]
    r_train, r_hold = split(rest)
    print(f"\n{BOLD}DOES THE SWEEP ADD ANYTHING?{RESET}  (sweep vs gap-downs without one)")
    train_gap = statistics.fmean(s_train) - statistics.fmean(r_train)
    hold_gap = statistics.fmean(s_hold) - statistics.fmean(r_hold)
    p = permutation_p(list(s_hold), list(r_hold), draws)
    print(f"  train {train_gap:+.3f}R   holdout {hold_gap:+.3f}R  p={p:.3f}"
          + ("   SIGN FLIPPED" if (train_gap > 0) != (hold_gap > 0) else ""))
    print(f"\n{BOLD}IS THE SWEEP TRADE PROFITABLE ON ITS OWN?{RESET}")
    for name, vals in (("train", s_train), ("holdout", s_hold)):
        mean = statistics.fmean(vals)
        se = statistics.stdev(vals) / len(vals) ** 0.5
        print(f"  {name:<8}{mean:+.3f}R  se {se:.3f}  t {mean / se if se else 0:+.2f}  n={len(vals)}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--store", default="data/intraday_alpaca")
    p.add_argument("--holdout", type=float, default=0.333)
    p.add_argument("--draws", type=int, default=20_000)
    a = p.parse_args()
    raise SystemExit(main(a.store, a.holdout, a.draws))
