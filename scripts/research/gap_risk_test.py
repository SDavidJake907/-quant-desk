"""Can a stop be honoured on this name, or does it gap straight through?

    python scripts/gap_risk_test.py
    python scripts/gap_risk_test.py --store data/intraday

READ-ONLY research over an accumulated store.

THE LEAK, WHICH IS LARGER THAN THE EDGE
Measured over 860 resolved setups on the 501-symbol Alpaca store:

    clean stop fills   n=442   mean -1.000R
    GAPPED through     n= 72   mean -1.684R      14.0% of all stops

    cost of gapping    -49.2R  =  -0.0573R per trade
    expectancy          +0.0538R

The strategy earns less per trade than gapping costs it. A stop that gaps does
not fill AT the stop, it fills at the open, and those 72 trades lost an extra
0.684R each. Removing that leak alone takes expectancy from +0.0538R to
+0.1110R without predicting anything.

WHY THIS IS A CONSTRAINT AND NOT THE SIXTEENTH RANKING IDEA
Fifteen ideas have failed here and every one tried either to say "this setup
beats that setup" or to engineer a better exit. The single thing that worked --
the 20M liquidity floor, p=0.022 -- did neither. It said where the MECHANISM
applies: this trades sweeps of resting orders, and a thin name has none.

This is the same shape, applied to risk instead of signal. A 1.5% stop is a
statement that losses are capped at 1R. On SNDK, whose mean overnight gap is
2.70%, that statement is simply false -- the stop is inside the noise of a
single gap and cannot be honoured. Roughly half these positions are held
overnight by design, so the stop has to survive an open.

    ratio = stop distance % / mean |overnight gap| %

Below 1 the stop sits inside a typical gap. Above 3 a gap rarely reaches it.
Bands fixed before any outcome was examined, and the threshold is not tuned.

POINT-IN-TIME, WHICH IS THE EASY THING TO GET WRONG
The gap statistic for a trade uses ONLY sessions strictly before its trigger
bar. Computing a symbol's average gap over the whole series and applying it to
a trade in the middle of that series leaks the future into the filter, and would
manufacture a result. A minimum history is required before a trade is scored at
all.

THE BAR EVERYTHING ELSE HAD TO CLEAR
Chosen on TRAIN, applied unchanged to HOLDOUT, permutation tested, and checked
for a sign flip between halves -- which is how the equal-lows, horizon and
sweep-shape results all disqualified themselves.
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.backtest.setups import resolve
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED
from scanner.screens.smc import scan

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
BANDS = ("stop inside gap", "1-2x gap", "2-3x gap", "3x+ gap")
#: Sessions of history required before a trade can be scored.
MIN_SESSIONS = 40


def session_index(bars) -> list[list[int]]:
    """Bar indexes grouped by session date, in order."""
    buckets: dict[object, list[int]] = defaultdict(list)
    for i, moment in enumerate(bars.times):
        buckets[moment.date()].append(i)
    return [buckets[day] for day in sorted(buckets)]


def running_gap_mean(bars, sessions: list[list[int]]) -> list[float | None]:
    """Mean |overnight gap| % using only sessions BEFORE each session.

    Returned per session position, aligned to `sessions`. None until
    MIN_SESSIONS of history exist.
    """
    out: list[float | None] = []
    seen: list[float] = []
    for position, indexes in enumerate(sessions):
        out.append(statistics.fmean(seen)
                   if len(seen) >= MIN_SESSIONS else None)
        if position + 1 < len(sessions):
            previous_close = bars.closes[indexes[-1]]
            next_open = bars.opens[sessions[position + 1][0]]
            if previous_close > 0:
                seen.append(abs(next_open / previous_close - 1.0) * 100.0)
    return out


def band(ratio: float) -> str:
    """Fixed before looking at a single outcome."""
    if ratio < 1.0:
        return "stop inside gap"
    if ratio < 2.0:
        return "1-2x gap"
    if ratio < 3.0:
        return "2-3x gap"
    return "3x+ gap"


def permutation_p(a: list[float], b: list[float], draws: int) -> float:
    if len(a) < 5 or len(b) < 5:
        return 1.0
    observed = abs(statistics.fmean(a) - statistics.fmean(b))
    pool, cut = a + b, len(a)
    rng = random.Random(0)
    hits = 0
    for _ in range(draws):
        rng.shuffle(pool)
        if abs(statistics.fmean(pool[:cut])
               - statistics.fmean(pool[cut:])) >= observed:
            hits += 1
    return hits / draws


def show(rows, label: str, min_group: int) -> list[str]:
    groups: dict[str, list[float]] = defaultdict(list)
    for name, r, _ in rows:
        groups[name].append(r)
    print(f"\n  {BOLD}{label}{RESET}")
    usable = []
    for name in BANDS:
        values = groups.get(name, [])
        if not values:
            continue
        mean = statistics.fmean(values)
        se = (statistics.stdev(values) / math.sqrt(len(values))
              if len(values) > 1 else 0.0)
        thin = "" if len(values) >= min_group else f"  {YELLOW}thin{RESET}"
        tint = GREEN if mean > 0 else RED
        print(f"    {name:<16}{tint}{mean:>+7.3f}R{RESET}  se {se:>5.3f}  "
              f"n={len(values):<5}{thin}")
        if len(values) >= min_group:
            usable.append(name)
    return usable


def main(store: str, holdout: float, draws: int, min_group: int) -> int:
    path = Path(store)
    history.STORE_DIR = path
    symbols = sorted({f.name.split("__")[0] for f in path.glob("*.json")})
    if not symbols:
        print(f"{RED}no symbols in {store}{RESET}")
        return 2

    rows = []
    gapped_by_band: dict[str, list[int]] = defaultdict(list)
    for symbol in symbols:
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        sessions = session_index(bars)
        means = running_gap_mean(bars, sessions)
        position_of = {i: p for p, idx in enumerate(sessions) for i in idx}
        for setup in scan(bars, k=V.swing_k,
                          require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct,
                          min_reward_risk=V.min_reward_risk):
            if crosses_session(bars, setup.trigger_index):
                continue
            risk = setup.entry_price - setup.stop_price
            if risk <= 0 or setup.entry_price <= 0:
                continue
            gap_mean = means[position_of[setup.trigger_index]]
            if not gap_mean:
                continue
            stop_pct = risk / setup.entry_price * 100.0
            name = band(stop_pct / gap_mean)
            outcome = resolve(
                bars, symbol=setup.symbol, entry_index=setup.trigger_index,
                entry_price=setup.entry_price, stop_price=setup.stop_price,
                target_price=setup.target_price, max_bars=V.max_hold_bars,
            )
            if outcome is None:
                continue
            rows.append((setup.trigger_index / len(bars), name,
                         outcome.r_multiple,
                         getattr(outcome, "gapped", False)))
            gapped_by_band[name].append(
                1 if getattr(outcome, "gapped", False) else 0)

    rows.sort(key=lambda row: row[0])
    scored = [(name, r, g) for _, name, r, g in rows]
    n = len(scored)
    cut = int(n * (1 - holdout))
    print(f"{DIM}{n} scored setups; train {cut}, holdout {n - cut}{RESET}")
    print(f"{DIM}ratio = stop distance % / mean |overnight gap| %, the gap mean "
          f"from sessions strictly BEFORE each trigger ({MIN_SESSIONS} "
          f"minimum){RESET}")

    print(f"\n  {BOLD}how often a stop actually gaps, by band{RESET}")
    for name in BANDS:
        flags = gapped_by_band.get(name, [])
        if flags:
            print(f"    {name:<16}{sum(flags) / len(flags) * 100:>6.1f}% of "
                  f"trades gapped   n={len(flags)}")

    usable = show(scored[:cut], "TRAIN - choose here", min_group)
    show(scored[cut:], "HOLDOUT - same bands, unchanged", min_group)

    print(f"\n{BOLD}DOES IT SURVIVE?{RESET}")
    if len(usable) < 2:
        print(f"  {YELLOW}fewer than two bands reach n>={min_group} on train "
              f"- undecidable{RESET}")
        return 0

    train: dict[str, list[float]] = defaultdict(list)
    for name, r, _ in scored[:cut]:
        train[name].append(r)
    best = max(usable, key=lambda b: statistics.fmean(train[b]))
    worst = min(usable, key=lambda b: statistics.fmean(train[b]))
    train_gap = statistics.fmean(train[best]) - statistics.fmean(train[worst])

    hold: dict[str, list[float]] = defaultdict(list)
    for name, r, _ in scored[cut:]:
        hold[name].append(r)
    a, b = hold.get(best, []), hold.get(worst, [])
    print(f"  train picked {BOLD}{best}{RESET} over {BOLD}{worst}{RESET}  "
          f"gap {train_gap:+.3f}R")
    if len(a) < min_group or len(b) < min_group:
        print(f"  {YELLOW}holdout bands too small ({len(a)} vs {len(b)})"
              f"{RESET}")
        return 0
    hold_gap = statistics.fmean(a) - statistics.fmean(b)
    p = permutation_p(list(a), list(b), draws)
    print(f"  holdout gap {hold_gap:+.3f}R  n={len(a)}/{len(b)}  p={p:.3f}")
    if (train_gap > 0) != (hold_gap > 0):
        print(f"  {RED}SIGN FLIPPED between halves - the signature of noise, "
              f"and the sixteenth time{RESET}")
    elif p < 0.05:
        print(f"  {GREEN}holds direction and clears p<0.05{RESET}")
        print(f"  {DIM}The second CONSTRAINT to survive, after the 20M floor. "
              f"Still one sample: it wants the 2018/2020/2022 drawdowns before "
              f"it gates anything.{RESET}")
    else:
        print(f"  {DIM}same direction, not significant{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="data/intraday_alpaca")
    parser.add_argument("--holdout", type=float, default=0.333)
    parser.add_argument("--draws", type=int, default=20_000)
    parser.add_argument("--min-group", type=int, default=10)
    args = parser.parse_args()
    raise SystemExit(main(args.store, args.holdout, args.draws,
                          args.min_group))
