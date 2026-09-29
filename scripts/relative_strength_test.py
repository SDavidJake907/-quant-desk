"""Does a sweep mean the same thing in a leader and in a falling knife?

    python scripts/relative_strength_test.py

READ-ONLY research over an accumulated store.

WHERE THIS CAME FROM
Both positions opened on 2026-09-28 looked like this:

              vs S&P 500 (12-1)   below 200-day EMA   12-month
    MLM            -29.2pp             -14.4%          -19.6%
    CMG            -21.2pp              -8.7%          -16.9%

Two deep laggards in daily downtrends, bought on a 30-minute structural pattern
that has no opinion about either. `_sector_standing()` already computes relative
strength on every scan -- and hands it to the ranking prompt as context, where
it informs an advisory opinion and gates nothing.

THE MECHANISM ARGUMENT
This trades liquidity sweeps. A sweep is supposed to be a SHAKEOUT: price runs
through a level where stops are clustered, fills them, and reverses because
buyers were waiting there. That story needs someone willing to defend the level.

In a name that has lagged the index by 29 points over a year, a break of the
lows may not be a shakeout at all -- it may simply be the selling continuing.
The pattern looks identical on the chart either way, which is exactly the kind
of distinction `took_out` cannot make: it compares one low to another and knows
nothing about who is on the other side.

This is the same SHAPE as the two things that have survived here. The 20M
liquidity floor (p=0.022) says a sweep needs resting orders, so thin names are
excluded. The regime filter (p=0.201) says a long-only sweep needs a market that
is not falling. Both are claims about where the MECHANISM applies. Neither
predicts anything.

Eighteen ranking ideas have failed. This is not one -- it does not say leaders
are better stocks, it says a sweep in a falling name may be a different event.

HOW IT IS MEASURED
12-1 momentum, the one cross-sectional number the repo already ranks on:
the return from 12 months ago to 1 month ago, skipping the most recent month
because short-horizon reversal contaminates it. Relative strength is the
stock's 12-1 minus the benchmark's, in percentage points.

    leader        RS >= +10pp
    in line       -10pp < RS < +10pp
    laggard       RS <= -10pp
    deep laggard  RS <= -25pp        <- where both of today's trades sit

Bands fixed before any outcome was looked at.

POINT-IN-TIME
Momentum for a setup on date D uses daily closes strictly BEFORE D, for the
stock and for ^GSPC alike. Computing it over the full series and applying it
backwards would leak the future into the filter.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.backtest.setups import resolve
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import SESSION_ALIGNED
from scanner.screens.smc import scan

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
BANDS = ("leader", "in line", "laggard", "deep laggard")
LOOKBACK, SKIP = 252, 21


def momentum_12_1(closes: list[float]) -> float | None:
    """Return from 12 months ago to 1 month ago, as a percentage."""
    if len(closes) < LOOKBACK + 1:
        return None
    start, end = closes[-LOOKBACK], closes[-SKIP]
    if start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def band(rs: float) -> str:
    """Fixed before looking at a single outcome."""
    if rs <= -25:
        return "deep laggard"
    if rs <= -10:
        return "laggard"
    if rs >= 10:
        return "leader"
    return "in line"


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
        print(f"    {name:<14}{tint}{mean:>+7.3f}R{RESET}  se {se:>5.3f}  "
              f"n={len(values):<5} win {win:>4.0f}%{thin}")
        if len(values) >= min_group:
            usable.append(name)
    return usable


async def main(store: str, holdout: float, draws: int, min_group: int) -> int:
    path = Path(store)
    history.STORE_DIR = path
    symbols = sorted({f.name.split("__")[0] for f in path.glob("*.json")})

    print(f"{DIM}fetching 5y daily for {len(symbols)} symbols + ^GSPC...{RESET}")
    async with httpx.AsyncClient(timeout=60) as client:
        benchmark = await fetch_bars("^GSPC", client, range_="5y")
        daily, _ = await fetch_many(tuple(symbols), client, range_="5y")
    bench_dates = list(benchmark.dates)
    bench_closes = list(benchmark.closes)
    print(f"{DIM}{len(daily)} daily series{RESET}")

    rows = []
    for symbol in symbols:
        series = daily.get(symbol)
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if series is None or bars is None or len(bars) < 60:
            continue
        dates = list(series.dates)
        closes = list(series.closes)
        for setup in scan(bars, k=V.swing_k,
                          require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct,
                          min_reward_risk=V.min_reward_risk):
            i = setup.trigger_index
            if crosses_session(bars, i):
                continue
            if setup.entry_price - setup.stop_price <= 0:
                continue
            when = bars.times[i].date()
            # Live never opens a Friday setup.
            if bars.times[i].weekday() >= V.wind_down_from_weekday:
                continue
            cut = sum(1 for d in dates if d < when)
            bcut = sum(1 for d in bench_dates if d < when)
            stock = momentum_12_1(closes[:cut])
            index = momentum_12_1(bench_closes[:bcut])
            if stock is None or index is None:
                continue
            outcome = resolve(
                bars, symbol=setup.symbol, entry_index=i,
                entry_price=setup.entry_price, stop_price=setup.stop_price,
                target_price=setup.target_price, max_bars=V.max_hold_bars,
            )
            if outcome:
                rows.append((i / len(bars), band(stock - index),
                             outcome.r_multiple))

    rows.sort(key=lambda row: row[0])
    scored = [(b, r) for _, b, r in rows]
    n = len(scored)
    if n < 60:
        print(f"{YELLOW}only {n} scored{RESET}")
        return 0
    cut = int(n * (1 - holdout))
    print(f"\n{DIM}{n} setups scored (Mon-Thu); train {cut}, "
          f"holdout {n - cut}{RESET}")
    print(f"{DIM}RS = stock 12-1 momentum minus ^GSPC, both point-in-time"
          f"{RESET}")
    mix: dict[str, int] = defaultdict(int)
    for b, _ in scored:
        mix[b] += 1
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
    hold: dict[str, list[float]] = defaultdict(list)
    for b, r in scored[cut:]:
        hold[b].append(r)
    a, b = hold.get(best, []), hold.get(worst, [])
    print(f"  train picked {BOLD}{best}{RESET} over {BOLD}{worst}{RESET}  "
          f"gap {train_gap:+.3f}R")
    if len(a) < min_group or len(b) < min_group:
        print(f"  {YELLOW}holdout bands too small ({len(a)} vs {len(b)}){RESET}")
        return 0
    hold_gap = statistics.fmean(a) - statistics.fmean(b)
    p = permutation_p(list(a), list(b), draws)
    print(f"  holdout gap {hold_gap:+.3f}R  n={len(a)}/{len(b)}  p={p:.3f}")
    if (train_gap > 0) != (hold_gap > 0):
        print(f"  {RED}SIGN FLIPPED — the signature of noise, and the "
              f"nineteenth time{RESET}")
    elif p < 0.05:
        print(f"  {GREEN}holds direction and clears p<0.05{RESET}")
        print(f"  {DIM}A third constraint, if it replicates. It needs the "
              f"2018/2020/2022 drawdowns before it gates anything live.{RESET}")
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
    raise SystemExit(asyncio.run(
        main(args.store, args.holdout, args.draws, args.min_group)))
