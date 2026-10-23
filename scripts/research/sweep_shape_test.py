"""Did the sweep get REJECTED, or did it just break down?

    python scripts/sweep_shape_test.py
    python scripts/sweep_shape_test.py --index all

READ-ONLY research over the accumulated store.

THE THING A HUMAN SEES AND THE CODE DOES NOT
`took_out` decides a sweep happened on one comparison:

    return prior is not None and prior.kind == LOW and low < prior.price

Only the LOW. Nothing about where the bar closed.

So two opposite chart pictures score identically. A bar that stabs below the
level on a long lower wick and closes back near its high is a rejection —
resting orders were filled and price was pushed straight back up, which is the
entire premise of trading a sweep. A bar that opens, falls, and closes on its
low has broken down; the level did not hold, and no discretionary trader
reading that chart would call it a sweep.

Twelve ranking ideas have failed here and every one was an INDICATOR — RSI,
MACD, ATR bands, weekday, session slot, liquidity tier, horizons. This is not
an indicator. It is the shape of a single candle, and it is the shape the
strategy's own thesis depends on.

HOW IT IS MEASURED
Close position within the sweep bar's own range:

    ratio = (close - low) / (high - low)

1.0 closed at the high, full rejection. 0.0 closed at the low, full breakdown.
Thirds, fixed before looking at any outcome.

The doji case — high equal to low — cannot happen here any more: those bars are
the 16:00 placeholders now rejected at parse time.

SAME BAR AS EVERYTHING ELSE
Chosen on TRAIN, applied unchanged to HOLDOUT, permutation tested, and checked
for a sign flip — which is how the RSI divergence, equal-lows and horizon
results all disqualified themselves.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import statistics
import sys
from collections import defaultdict

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.backtest.setups import resolve
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}
V = TradingPolicy()
BANDS = ("closed weak", "closed mid", "closed strong")


def shape(bars, index: int) -> tuple[str, float] | None:
    """Where the sweeping bar closed inside its own range."""
    high, low, close = bars.highs[index], bars.lows[index], bars.closes[index]
    span = high - low
    if span <= 0:
        return None
    ratio = (close - low) / span
    if ratio < 1 / 3:
        return "closed weak", ratio
    if ratio < 2 / 3:
        return "closed mid", ratio
    return "closed strong", ratio


def permutation_p(a: list[float], b: list[float], draws: int) -> float:
    if len(a) < 5 or len(b) < 5:
        return 1.0
    observed = abs(statistics.fmean(a) - statistics.fmean(b))
    pool, cut = a + b, len(a)
    rng = random.Random(0)
    hits = 0
    for _ in range(draws):
        rng.shuffle(pool)
        if abs(statistics.fmean(pool[:cut]) - statistics.fmean(pool[cut:])) >= observed:
            hits += 1
    return hits / draws


def show(rows, label: str, min_group: int) -> None:
    groups: dict[str, list[float]] = defaultdict(list)
    for band, r in rows:
        groups[band].append(r)
    print(f"\n  {BOLD}{label}{RESET}")
    for band in BANDS:
        values = groups.get(band, [])
        if not values:
            continue
        mean = statistics.fmean(values)
        se = (statistics.stdev(values) / math.sqrt(len(values))
              if len(values) > 1 else 0.0)
        thin = "" if len(values) >= min_group else f"  {YELLOW}thin{RESET}"
        tint = GREEN if mean > 0 else RED
        print(f"    {band:<14}{tint}{mean:>+7.3f}R{RESET}  se {se:>5.3f}  "
              f"n={len(values):<4}{thin}")


async def main(index: str, holdout: float, draws: int, min_group: int) -> int:
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])

    found = []
    for member in universe:
        bars = history.accumulated(member.symbol, V.interval_minutes,
                                   SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        for setup in scan(bars, k=V.swing_k,
                          require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct,
                          min_reward_risk=V.min_reward_risk):
            if crosses_session(bars, setup.trigger_index):
                continue
            found.append((bars, setup))

    found.sort(key=lambda row: row[1].trigger_index / len(row[0]))
    rows, ratios = [], []
    for bars, setup in found:
        got = shape(bars, setup.sweep_index)
        if got is None:
            continue
        outcome = resolve(
            bars, symbol=setup.symbol, entry_index=setup.trigger_index,
            entry_price=setup.entry_price, stop_price=setup.stop_price,
            target_price=setup.target_price, max_bars=V.max_hold_bars,
        )
        if outcome is None:
            continue
        rows.append((got[0], outcome.r_multiple))
        ratios.append(got[1])

    cut = int(len(rows) * (1 - holdout))
    train, hold = rows[:cut], rows[cut:]
    print(f"{DIM}{len(rows)} resolved setups; train {len(train)}, "
          f"holdout {len(hold)}{RESET}")
    print(f"{DIM}sweep-bar close position: median "
          f"{statistics.median(ratios):.2f} of range{RESET}")

    counts = defaultdict(int)
    for band, _ in rows:
        counts[band] += 1
    print(f"{DIM}shape mix: " + ", ".join(
        f"{b} {counts[b]}" for b in BANDS if counts[b]) + f"{RESET}")

    show(train, "TRAIN — choose here", min_group)
    show(hold, "HOLDOUT — same bands, unchanged", min_group)

    tr: dict[str, list[float]] = defaultdict(list)
    for band, r in train:
        tr[band].append(r)
    usable = [b for b in BANDS if len(tr.get(b, [])) >= min_group]
    print(f"\n{BOLD}DOES IT SURVIVE?{RESET}")
    if len(usable) < 2:
        print(f"  {YELLOW}fewer than two bands reach n>={min_group} on train "
              f"— undecidable{RESET}")
        return 0

    best = max(usable, key=lambda b: statistics.fmean(tr[b]))
    worst = min(usable, key=lambda b: statistics.fmean(tr[b]))
    train_gap = statistics.fmean(tr[best]) - statistics.fmean(tr[worst])

    ho: dict[str, list[float]] = defaultdict(list)
    for band, r in hold:
        ho[band].append(r)
    a, b2 = ho.get(best, []), ho.get(worst, [])
    print(f"  train picked {BOLD}{best}{RESET} over {BOLD}{worst}{RESET}"
          f"  gap {train_gap:+.3f}R")
    if len(a) < min_group or len(b2) < min_group:
        print(f"  {YELLOW}holdout bands too small ({len(a)} vs {len(b2)})"
              f"{RESET}")
        return 0
    hold_gap = statistics.fmean(a) - statistics.fmean(b2)
    p = permutation_p(list(a), list(b2), draws)
    print(f"  holdout gap {hold_gap:+.3f}R  n={len(a)}/{len(b2)}  p={p:.3f}")
    if (train_gap > 0) != (hold_gap > 0):
        print(f"  {RED}SIGN FLIPPED — the signature of noise, and the "
              f"thirteenth time{RESET}")
    elif p < 0.05:
        print(f"  {GREEN}holds direction and clears p<0.05{RESET}")
        print(f"  {DIM}Treat as a hypothesis for a second sample, not a "
              f"result. It is also the first CHART-SHAPE idea tested here, "
              f"rather than the thirteenth indicator.{RESET}")
    else:
        print(f"  {DIM}same direction, not significant{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--holdout", type=float, default=0.333)
    p.add_argument("--draws", type=int, default=20_000)
    p.add_argument("--min-group", type=int, default=10)
    a = p.parse_args()
    raise SystemExit(asyncio.run(
        main(a.index, a.holdout, a.draws, a.min_group)))
