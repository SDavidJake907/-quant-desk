"""Is a 1.5% stop a real level, or is it inside the noise?

    python scripts/stop_vs_noise.py
    python scripts/stop_vs_noise.py --index all

READ-ONLY research over the accumulated store.

THE IDEA, AND WHY IT IS A DIFFERENT SHAPE FROM THE ELEVEN THAT FAILED
Every ranking idea tried here has died the same way: RSI divergence, bearish
exits, take-profit ladders, four shadow dimensions, four horizons, delayed
entry, and — most tellingly — the strategy's own thesis that a POOL of equal
lows beats an isolated one, which went +0.616R on train and -0.674R on
holdout. Eleven attempts to say "this setup is better than that one", eleven
nulls, several of them flipping sign between halves.

One thing did work: the 20M liquidity floor, +0.114R below against +0.776R
above, p=0.022, the strongest result this project has produced. It is not a
prediction. It is a statement about where the strategy's MECHANISM applies:
this trades liquidity sweeps, a sweep needs resting orders, and a thin name
does not have them. Outside that universe the pattern matches noise.

This asks the same kind of question about a different mechanism.

`min_risk_pct = 1.5` applies one number to every name. On a quiet utility a
1.5% stop sits well outside normal movement, so being hit means the structure
actually failed. On a high-beta name whose bars routinely travel 3%, the same
stop sits INSIDE one bar's ordinary range — it gets taken out by drift rather
than by the setup being wrong, and the trade was never really given a chance.

So the measure is not the stop's size but its size RELATIVE TO THE NAME'S OWN
NOISE:

    ratio = stop distance / ATR at the trigger bar

Below 1, the stop is inside a typical bar. Above 2, it is outside anything
routine. Bands are fixed before looking, and the threshold is not tuned.

WHAT WOULD MAKE THIS REAL
The same bar everything else had to clear: chosen on TRAIN, applied unchanged
to HOLDOUT, permutation tested, and NOT flipping sign between halves. Eleven
ideas have failed that bar. The prior is that this one does too.
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
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}
V = TradingPolicy()
ATR_WINDOW = 14


def atr(bars, index: int, window: int = ATR_WINDOW) -> float | None:
    """Average true range over the bars UP TO the trigger, never past it."""
    start = index - window
    if start < 1:
        return None
    ranges = []
    for i in range(start, index + 1):
        prior = bars.closes[i - 1]
        ranges.append(max(bars.highs[i] - bars.lows[i],
                          abs(bars.highs[i] - prior),
                          abs(bars.lows[i] - prior)))
    return sum(ranges) / len(ranges) if ranges else None


def band(ratio: float | None) -> str:
    """Fixed before looking at a single outcome."""
    if ratio is None:
        return "unknown"
    if ratio < 1.0:
        return "inside noise"
    if ratio < 2.0:
        return "1-2x ATR"
    if ratio < 3.0:
        return "2-3x ATR"
    return "3x+ ATR"


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


def show(rows, label: str, min_group: int) -> list[str]:
    groups: dict[str, list[float]] = defaultdict(list)
    for b, r in rows:
        groups[b].append(r)
    print(f"\n  {BOLD}{label}{RESET}")
    order = ["inside noise", "1-2x ATR", "2-3x ATR", "3x+ ATR"]
    present = []
    for b in order:
        values = groups.get(b, [])
        if not values:
            continue
        mean = statistics.fmean(values)
        se = (statistics.stdev(values) / math.sqrt(len(values))
              if len(values) > 1 else 0.0)
        flag = "" if len(values) >= min_group else f"  {YELLOW}thin{RESET}"
        tint = GREEN if mean > 0 else RED
        print(f"    {b:<14}{tint}{mean:>+7.3f}R{RESET}  se {se:>5.3f}  "
              f"n={len(values):<4}{flag}")
        if len(values) >= min_group:
            present.append(b)
    return present


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
    rows = []
    for bars, setup in found:
        outcome = resolve(
            bars, symbol=setup.symbol, entry_index=setup.trigger_index,
            entry_price=setup.entry_price, stop_price=setup.stop_price,
            target_price=setup.target_price, max_bars=V.max_hold_bars,
        )
        if outcome is None:
            continue
        a = atr(bars, setup.trigger_index)
        ratio = None if not a else (setup.entry_price - setup.stop_price) / a
        rows.append((band(ratio), outcome.r_multiple))

    cut = int(len(rows) * (1 - holdout))
    train, hold = rows[:cut], rows[cut:]
    print(f"{DIM}{len(rows)} resolved setups; train {len(train)}, "
          f"holdout {len(hold)}{RESET}")
    print(f"{DIM}ratio = stop distance / ATR({ATR_WINDOW}) at the trigger"
          f"{RESET}")

    usable = show(train, "TRAIN — choose here", min_group)
    show(hold, "HOLDOUT — same bands, unchanged", min_group)

    print(f"\n{BOLD}DOES IT SURVIVE?{RESET}")
    if len(usable) < 2:
        print(f"  {YELLOW}fewer than two bands reach n>={min_group} on "
              f"train — undecidable{RESET}")
        return 0

    tr: dict[str, list[float]] = defaultdict(list)
    for b, r in train:
        tr[b].append(r)
    best = max(usable, key=lambda b: statistics.fmean(tr[b]))
    worst = min(usable, key=lambda b: statistics.fmean(tr[b]))
    train_gap = statistics.fmean(tr[best]) - statistics.fmean(tr[worst])

    ho: dict[str, list[float]] = defaultdict(list)
    for b, r in hold:
        ho[b].append(r)
    a, b2 = ho.get(best, []), ho.get(worst, [])
    print(f"  train picked {BOLD}{best}{RESET} over {BOLD}{worst}{RESET}"
          f"  gap {train_gap:+.3f}R")
    if len(a) < min_group or len(b2) < min_group:
        print(f"  {YELLOW}holdout bands too small ({len(a)} vs {len(b2)})"
              f"{RESET}")
        return 0
    hold_gap = statistics.fmean(a) - statistics.fmean(b2)
    p = permutation_p(list(a), list(b2), draws)
    flipped = (train_gap > 0) != (hold_gap > 0)
    print(f"  holdout gap {hold_gap:+.3f}R  n={len(a)}/{len(b2)}  p={p:.3f}")
    if flipped:
        print(f"  {RED}SIGN FLIPPED between halves — the signature of noise, "
              f"and the twelfth time{RESET}")
    elif p < 0.05:
        print(f"  {GREEN}holds direction and clears p<0.05. The first ranking "
              f"idea here to do so — treat it as a hypothesis worth a second "
              f"sample, not a result{RESET}")
    else:
        print(f"  {DIM}same direction, not significant. Not a finding; not "
              f"dead either{RESET}")
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
