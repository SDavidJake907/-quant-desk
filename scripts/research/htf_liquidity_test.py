"""Did the sweep take out a level anyone was watching?

    python scripts/htf_liquidity_test.py
    python scripts/htf_liquidity_test.py --entry 30 --htf 240

READ-ONLY research over an accumulated store.

THIS IS NOT ANOTHER FILTER
Nineteen ideas have been tested here and nearly all were filters: an
independent yes/no, AND-ed onto the others. Stack enough and the funnel narrows
without improving, which is the state the scanner is in.

This changes what a SWEEP IS, rather than judging a setup after the fact.

    took_out today:  low < the previous swing low ON THE ENTRY TIMEFRAME

On 15m bars that fires constantly, and most of those lows are meaningless --
nobody's stops are resting at a minor 15m pivot. The strategy's own thesis is
that a sweep runs RESTING ORDERS and reverses because buyers were waiting. That
requires a level people can actually see.

So the higher timeframe does not veto the setup. It SUPPLIES THE LEVEL the
sweep has to take out:

    4h     a confirmed swing low -- where stops accumulate
    15m    the sweep of that level, then CHoCH, then BOS
    5m     entry precision inside the 15m signal        (not yet collected)

WHY THIS HAS THE BEST PRIOR OF ANYTHING TRIED HERE
The one result that survived is the 20M liquidity floor, p=0.022: a sweep needs
resting orders, so thin NAMES do not qualify. This is the identical argument
applied to the level instead of the instrument: a sweep needs resting orders,
so insignificant LEVELS do not qualify. It is a refinement of the mechanism the
strategy already claims, not a new predictor bolted on.

POINT-IN-TIME
A 4h swing low at index j is only confirmed at j + k, so a sweep may only be
credited against 4h lows confirmed strictly BEFORE the sweep bar. Crediting it
against a level the market had not yet drawn would leak the future.

BANDS, FIXED BEFORE LOOKING
    htf pool      the sweep price is below a confirmed 4h swing low
    entry only    it took out an entry-timeframe low and nothing higher
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
from scanner.features.intraday import IntradayBars, SESSION_ALIGNED
from scanner.features.structure import swings
from scanner.screens.smc import LOW, known_at, scan

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
BANDS = ("htf pool", "entry only")
HOLD_MINUTES = V.max_hold_bars * 30


def coarsen(bars: IntradayBars, minutes: int) -> IntradayBars | None:
    """Fold into `minutes` bars, never across a session boundary."""
    if minutes == bars.interval_minutes:
        return bars
    step = minutes // bars.interval_minutes
    if step < 1 or minutes % bars.interval_minutes:
        return None
    by_day: dict[object, list[int]] = defaultdict(list)
    for i, moment in enumerate(bars.times):
        by_day[moment.date()].append(i)
    times, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    for day in sorted(by_day):
        idx = by_day[day]
        for start in range(0, len(idx), step):
            chunk = idx[start:start + step]
            times.append(bars.times[chunk[0]])
            opens.append(bars.opens[chunk[0]])
            highs.append(max(bars.highs[i] for i in chunk))
            lows.append(min(bars.lows[i] for i in chunk))
            closes.append(bars.closes[chunk[-1]])
            volumes.append(sum(bars.volumes[i] for i in chunk))
    if len(times) < 30:
        return None
    return IntradayBars(
        symbol=bars.symbol, interval_minutes=minutes, alignment=bars.alignment,
        times=tuple(times), opens=tuple(opens), highs=tuple(highs),
        lows=tuple(lows), closes=tuple(closes), volumes=tuple(volumes))


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
    for name, r in rows:
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
        win = sum(1 for r in values if r > 0) / len(values) * 100
        print(f"    {name:<12}{tint}{mean:>+7.3f}R{RESET}  se {se:>5.3f}  "
              f"n={len(values):<5} win {win:>4.0f}%{thin}")
        if len(values) >= min_group:
            usable.append(name)
    return usable


def main(store: str, entry: int, htf: int, holdout: float,
         draws: int, min_group: int) -> int:
    path = Path(store)
    history.STORE_DIR = path
    symbols = sorted({f.name.split("__")[0] for f in path.glob("*.json")})
    hold = max(1, HOLD_MINUTES // entry)
    print(f"{DIM}entry {entry}m, liquidity pool from {htf}m, hold {hold} bars "
          f"({HOLD_MINUTES} minutes){RESET}")

    rows = []
    for symbol in symbols:
        bars = history.accumulated(symbol, entry, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        high_tf = coarsen(bars, htf)
        if high_tf is None:
            continue
        htf_points = swings(high_tf.highs, high_tf.lows, V.swing_k)
        # Map each HTF swing low to the time it was CONFIRMED, not formed.
        confirmed: list[tuple[object, float]] = []
        for point in htf_points:
            if point.kind != LOW:
                continue
            seen = point.index + V.swing_k
            if seen < len(high_tf):
                confirmed.append((high_tf.times[seen], point.price))
        confirmed.sort(key=lambda row: row[0])
        if not confirmed:
            continue

        for setup in scan(bars, k=V.swing_k,
                          require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct,
                          min_reward_risk=V.min_reward_risk):
            i = setup.trigger_index
            if crosses_session(bars, i):
                continue
            if setup.entry_price - setup.stop_price <= 0:
                continue
            if bars.times[i].weekday() >= V.wind_down_from_weekday:
                continue
            when = bars.times[setup.sweep_index]
            # The most recent HTF low CONFIRMED before the sweep bar.
            level = None
            for moment, price in confirmed:
                if moment < when:
                    level = price
                else:
                    break
            band = ("htf pool" if level is not None
                    and setup.sweep_price < level else "entry only")
            outcome = resolve(
                bars, symbol=setup.symbol, entry_index=i,
                entry_price=setup.entry_price, stop_price=setup.stop_price,
                target_price=setup.target_price, max_bars=hold)
            if outcome:
                rows.append((i / len(bars), band, outcome.r_multiple))

    rows.sort(key=lambda row: row[0])
    scored = [(b, r) for _, b, r in rows]
    n = len(scored)
    if n < 60:
        print(f"{YELLOW}only {n} scored{RESET}")
        return 0
    cut = int(n * (1 - holdout))
    mix: dict[str, int] = defaultdict(int)
    for b, _ in scored:
        mix[b] += 1
    print(f"{DIM}{n} setups; train {cut}, holdout {n - cut}{RESET}")
    print(f"{DIM}mix: " + ", ".join(f"{b} {mix[b]} ({mix[b]/n*100:.0f}%)"
                                    for b in BANDS if mix[b]) + f"{RESET}")

    usable = show(scored[:cut], "TRAIN - choose here", min_group)
    show(scored[cut:], "HOLDOUT - same bands, unchanged", min_group)

    print(f"\n{BOLD}DOES IT SURVIVE?{RESET}")
    if len(usable) < 2:
        print(f"  {YELLOW}fewer than two bands reach n>={min_group}{RESET}")
        return 0
    train: dict[str, list[float]] = defaultdict(list)
    for b, r in scored[:cut]:
        train[b].append(r)
    best = max(usable, key=lambda b: statistics.fmean(train[b]))
    worst = min(usable, key=lambda b: statistics.fmean(train[b]))
    train_gap = statistics.fmean(train[best]) - statistics.fmean(train[worst])
    hold_g: dict[str, list[float]] = defaultdict(list)
    for b, r in scored[cut:]:
        hold_g[b].append(r)
    a, b = hold_g.get(best, []), hold_g.get(worst, [])
    print(f"  train picked {BOLD}{best}{RESET} over {BOLD}{worst}{RESET}  "
          f"gap {train_gap:+.3f}R")
    if len(a) < min_group or len(b) < min_group:
        print(f"  {YELLOW}holdout too small ({len(a)} vs {len(b)}){RESET}")
        return 0
    hold_gap = statistics.fmean(a) - statistics.fmean(b)
    p = permutation_p(list(a), list(b), draws)
    print(f"  holdout gap {hold_gap:+.3f}R  n={len(a)}/{len(b)}  p={p:.3f}")
    if (train_gap > 0) != (hold_gap > 0):
        print(f"  {RED}SIGN FLIPPED — noise, and the twentieth time{RESET}")
    elif p < 0.05:
        print(f"  {GREEN}holds direction and clears p<0.05{RESET}")
        print(f"  {DIM}This is a change to what a sweep IS, so if it holds it "
              f"belongs in the screen rather than beside it.{RESET}")
    else:
        print(f"  {DIM}same direction, not significant{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="data/intraday_alpaca")
    parser.add_argument("--entry", type=int, default=15)
    parser.add_argument("--htf", type=int, default=240)
    parser.add_argument("--holdout", type=float, default=0.333)
    parser.add_argument("--draws", type=int, default=20_000)
    parser.add_argument("--min-group", type=int, default=10)
    args = parser.parse_args()
    raise SystemExit(main(args.store, args.entry, args.htf,
                          args.holdout, args.draws, args.min_group))
