"""Does a sweep mean the same thing at VIX 13 and VIX 30?

    python scripts/volatility_regime_test.py

READ-ONLY research over an accumulated store.

WHERE THIS CAME FROM
The morning scan (`LLM-Quant-Engine/logs/morning_scan.pdf`) reports, every day,
a market state this scanner is entirely blind to:

    10-Year Yield  +1.0% today, +12.1% this month -- a two-decade high
    VIX            +6.9% today, +10.2% this month
    Gold           -3.5%, -10.6%
    Financials     -6.2% this month     Cons. Discretionary  -5.6%

The screen sees one stock's 30-minute swing structure and nothing else. When
yields spike and financials bleed, a sweep on an S&P name scores exactly as it
would on a quiet day.

WHY THIS IS THE RIGHT PLACE TO LOOK, AND NOT THE TWENTY-SECOND FILTER
Twenty-one ideas have failed here. Thirteen tried to rank one setup against
another and every one flipped sign between halves. The two that did NOT die are
both statements about MARKET STATE rather than stock quality:

    20M liquidity floor   p=0.022   a sweep needs resting orders
    S&P regime filter     p=0.201   below both averages, long-only loses
                                    -0.086R n=179, and it HELD its value when
                                    the sample grew 57%

That asymmetry is the whole reason to run this. Volatility regime is the same
category as the one thing that did not fail.

THE MECHANISM
A sweep is a shakeout: price runs stops and reverses because buyers were
waiting. At VIX 13 a 1.5% stop sits well outside ordinary movement, so being
hit means the structure genuinely failed. At VIX 30 the same stop is inside a
single day's noise -- it gets taken out by drift, and the "sweep" is just the
tape being violent. Same chart, different event.

BANDS, FIXED BEFORE ANY OUTCOME WAS EXAMINED
Conventional VIX levels, not thresholds chosen from this data:

    calm       < 15
    normal     15 - 20
    elevated   20 - 30
    stressed   >= 30

POINT-IN-TIME
VIX for a setup on date D is the close of the last session STRICTLY BEFORE D.
Using D's own close would let the day's outcome leak into the filter.

TRIAL COST, STATED HONESTLY
Declared trials are near seventy and a Bonferroni threshold sits around 0.0007.
Nothing here will clear that. Read the train/holdout agreement and the sign,
not the p-value, and treat a survival as a hypothesis for a second sample.
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
from scanner.features.bars import fetch_bars
from scanner.features.intraday import SESSION_ALIGNED
from scanner.screens.smc import scan

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
BANDS = ("calm", "normal", "elevated", "stressed")


def band(vix: float) -> str:
    if vix < 15:
        return "calm"
    if vix < 20:
        return "normal"
    if vix < 30:
        return "elevated"
    return "stressed"


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
        print(f"    {name:<11}{tint}{mean:>+7.3f}R{RESET}  se {se:>5.3f}  "
              f"n={len(values):<5} win {win:>4.0f}%{thin}")
        if len(values) >= min_group:
            usable.append(name)
    return usable


async def main(store: str, holdout: float, draws: int, min_group: int) -> int:
    path = Path(store)
    history.STORE_DIR = path
    symbols = sorted({f.name.split("__")[0] for f in path.glob("*.json")})

    print(f"{DIM}fetching ^VIX...{RESET}")
    async with httpx.AsyncClient(timeout=60) as client:
        vix = await fetch_bars("^VIX", client, range_="5y")
    closes = {d: c for d, c in zip(vix.dates, vix.closes) if c and c > 0}
    days = sorted(closes)
    print(f"{DIM}{len(days)} VIX sessions, {closes[days[0]]:.1f} to "
          f"{closes[days[-1]]:.1f}, median {statistics.median(closes.values()):.1f}"
          f"{RESET}")

    rows = []
    for symbol in symbols:
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
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
            when = bars.times[i].date()
            # STRICTLY BEFORE: the day's own close cannot inform the filter.
            prior = [d for d in days if d < when]
            if not prior:
                continue
            level = closes[prior[-1]]
            outcome = resolve(
                bars, symbol=setup.symbol, entry_index=i,
                entry_price=setup.entry_price, stop_price=setup.stop_price,
                target_price=setup.target_price, max_bars=V.max_hold_bars)
            if outcome:
                rows.append((i / len(bars), band(level), outcome.r_multiple))

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
    print(f"\n{DIM}{n} setups; train {cut}, holdout {n - cut}{RESET}")
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
        print(f"  {YELLOW}holdout too small ({len(a)} vs {len(b)}){RESET}")
        return 0
    hold_gap = statistics.fmean(a) - statistics.fmean(b)
    p = permutation_p(list(a), list(b), draws)
    print(f"  holdout gap {hold_gap:+.3f}R  n={len(a)}/{len(b)}  p={p:.3f}")
    if (train_gap > 0) != (hold_gap > 0):
        print(f"  {RED}SIGN FLIPPED — noise, and the twenty-second time{RESET}")
    elif p < 0.05:
        print(f"  {GREEN}holds direction and clears p<0.05{RESET}")
        print(f"  {DIM}Market state, like the two results that did not die. "
              f"A hypothesis for a second sample, not a finding.{RESET}")
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
