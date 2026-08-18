"""Which of the trend screen's six criteria actually earn their place?

    python scripts/screen_ablation.py --index sp500 --limit 250

THE QUESTION
The screen is six conditions joined by AND, adopted together and never tested
apart. Three outcomes are possible for each one and only the first is the one
everybody assumes:

    it selects better trades          -> keep it
    it selects nothing in particular  -> it is costing signals for nothing
    it selects WORSE trades           -> it is the reason the screen loses

The third is not hypothetical here. Two of the six -- within 25% of the 52-week
high, and 30% above the 52-week low -- select for names that have already run.
Buying extension and then placing a stop under it is a plausible mechanism for
a screen that underperforms random entries in the same names.

HOW TO READ IT
`alone` runs each criterion as the entire screen. `without` runs the other five
and drops it. If dropping a criterion RAISES the edge, that criterion is doing
harm. Both are measured against random entries in the same names with the same
stop width and the same management, because absolute R says nothing here: every
one of these names is in an index that went up.

SELECTION BIAS, STATED UP FRONT
Thirteen variants are read at once. The best of thirteen is biased upward, so a
winner here is a hypothesis to take to a holdout, not a finding.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import statistics

import httpx

from scanner.backtest.costs import CostModel
from scanner.backtest.ladder import ladder_r
from scanner.features.bars import fetch_bars
from scanner.screens import momentum
from scanner.sources import composite
from scanner.sources.index_history import SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

BENCHMARK = "^GSPC"
INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,)}
DEFAULT_BASKET = ("MU", "NVDA", "AMD", "AVGO", "TSM", "AAPL", "MSFT", "META",
                  "AMZN", "GOOGL", "TSLA", "NFLX", "CRM", "ORCL", "QCOM")

#: Short labels, so a table of thirteen rows stays readable.
SHORT = {
    "price > 50d MA": "px > 50d",
    "50d > 150d > 200d": "MA stack",
    "200d MA rising": "200d rising",
    "within 25% of 52w high": "near 52w high",
    "30%+ above 52w low": "above 52w low",
    "outperforming index": "rel strength",
}

#: The management that measured best: no target, let the runner run.
RUNGS, TARGET_R = None, None


def _leg_cost_r(entry: float, stop: float, costs: CostModel) -> float:
    stop_fraction = (entry - stop) / entry
    return 0.0 if stop_fraction <= 0 else (costs.per_side_bps / 10_000.0) / stop_fraction


async def collect(symbols, range_):
    async with httpx.AsyncClient(timeout=60) as client:
        benchmark = await fetch_bars(BENCHMARK, client, range_=range_)
        results = await asyncio.gather(
            *(fetch_bars(s, client, range_=range_) for s in symbols),
            return_exceptions=True,
        )
    loaded = {s: r for s, r in zip(symbols, results) if not isinstance(r, Exception)}
    return loaded, benchmark, len(symbols) - len(loaded)


def _stats(rs):
    if len(rs) < 2:
        return 0.0, 0.0
    return statistics.fmean(rs), statistics.stdev(rs) / math.sqrt(len(rs))


def _score(bars, index, entry, stop, max_days, costs):
    return ladder_r(bars, entry_index=index, entry_price=entry, stop_price=stop,
                    target_price=TARGET_R, rungs=RUNGS, max_bars=max_days,
                    leg_cost_r=_leg_cost_r(entry, stop, costs))


def run_variant(bars_by_symbol, benchmark, required, atr_multiple, max_days,
                draws, costs, window=(0.0, 1.0)):
    lower, upper = window
    real, control = [], []
    for bars in bars_by_symbol.values():
        setups = momentum.detect(bars, benchmark, atr_multiple=atr_multiple,
                                 reward_risk=2.0, required=required)
        low, high = momentum.MIN_HISTORY - 1, len(bars) - 1
        if high <= low:
            continue
        # The split selects which signals COUNT; it never changes what the
        # screen saw, so a holdout boundary cannot alter the signals themselves.
        first = int(len(bars) * lower)
        last = int(len(bars) * upper)
        low, high = max(low, first), min(high, max(last, first + 1))
        if high <= low:
            continue
        for setup in setups:
            if not (first <= setup.trigger_index < last):
                continue
            entry, stop = setup.entry_price, setup.stop_price
            r = _score(bars, setup.trigger_index, entry, stop, max_days, costs)
            if r is None:
                continue
            real.append(r)
            stop_fraction = (entry - stop) / entry
            rng = random.Random(setup.trigger_index)
            for _ in range(draws):
                i = rng.randrange(low, high)
                c_entry = bars.closes[i]
                c_r = _score(bars, i, c_entry, c_entry * (1 - stop_fraction),
                             max_days, costs)
                if c_r is not None:
                    control.append(c_r)
    return real, control


def _row(label, real, control):
    mean, se = _stats(real)
    c_mean, _ = _stats(control)
    edge = mean - c_mean
    colour = GREEN if edge > 0 else RED
    # Edge is a difference of two means; its error is dominated by the smaller
    # sample, which is always the pattern. se below is the pattern's alone.
    print(f"    {label:<26}{len(real):>7}  {mean:>+7.3f}R  se {se:>5.3f}"
          f"   random {c_mean:>+7.3f}R   {colour}edge {edge:>+7.3f}R{RESET}")


LONG_NAME = {v: k for k, v in SHORT.items()}


async def confirm(bars_by_symbol, benchmark, required, atr_multiple, max_days,
                  draws, costs, holdout):
    """One chosen subset, split chronologically.

    The ablation reads thirteen variants over the whole series, so its winner
    is the best of thirteen and biased upward by construction. This re-runs a
    single pre-chosen subset with a train/holdout boundary, which is the only
    thing that can tell the difference between an effect and a lucky slice.
    """
    label = ", ".join(sorted(SHORT[c] for c in required)) if required else "all six"
    print(f"\n{BOLD}HOLDOUT CONFIRMATION{RESET} {DIM}({label}){RESET}")
    for name, window in (("train", (0.0, 1.0 - holdout)),
                         ("holdout", (1.0 - holdout, 1.0))):
        real, control = run_variant(bars_by_symbol, benchmark, required,
                                    atr_multiple, max_days, draws, costs, window)
        _row(name, real, control)


async def main(symbols, atr_multiple, range_, max_days, draws, costs,
               required=None, holdout=None):
    print(f"{DIM}fetching {len(symbols)} symbols + {BENCHMARK}, daily bars, {range_}{RESET}")
    bars_by_symbol, benchmark, missing = await collect(symbols, range_)
    if not bars_by_symbol:
        raise SystemExit("no price history loaded")
    print(f"{DIM}loaded {len(bars_by_symbol)} symbols ({missing} unavailable); "
          f"stop {atr_multiple:g} x ATR; no target, runner held to {max_days} sessions{RESET}")

    if holdout is not None:
        await confirm(bars_by_symbol, benchmark, required, atr_multiple,
                      max_days, draws, costs, holdout)
        return

    order = sorted(momentum.CRITERIA, key=lambda c: SHORT[c])

    print(f"\n{BOLD}ALL SIX (the screen as it stands){RESET}")
    real, control = run_variant(bars_by_symbol, benchmark, None, atr_multiple,
                                max_days, draws, costs)
    _row("all six", real, control)

    print(f"\n{BOLD}EACH CRITERION ALONE{RESET} {DIM}(is it worth anything by itself?){RESET}")
    for name in order:
        real, control = run_variant(bars_by_symbol, benchmark, frozenset({name}),
                                    atr_multiple, max_days, draws, costs)
        _row(SHORT[name], real, control)

    print(f"\n{BOLD}LEAVE ONE OUT{RESET} {DIM}(a rising edge means the dropped rule was harmful){RESET}")
    for name in order:
        rest = frozenset(momentum.CRITERIA - {name})
        real, control = run_variant(bars_by_symbol, benchmark, rest, atr_multiple,
                                    max_days, draws, costs)
        _row(f"without {SHORT[name]}", real, control)

    print(f"\n{YELLOW}Reading this table:{RESET}")
    print(f"{DIM}  - edge is the only column that matters; absolute R is positive{RESET}")
    print(f"{DIM}    for everything because these names are index survivors{RESET}")
    print(f"{DIM}  - 13 variants are read at once, so the best is biased upward{RESET}")
    print(f"{DIM}  - a looser screen gives more signals and a smaller se; compare{RESET}")
    print(f"{DIM}    edges, not confidence{RESET}")
    print(f"{DIM}  - delisted names are absent, so every row reads high{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--index", choices=list(INDEXES), default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--atr-multiple", type=float, default=2.0)
    p.add_argument("--range", dest="range_", default="10y")
    p.add_argument("--max-days", type=int, default=60)
    p.add_argument("--draws", type=int, default=2)
    p.add_argument("--spread-bps", type=float, default=5.0)
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--required", nargs="+", default=None,
                   help="short names of criteria to require, e.g. '200d rising'")
    p.add_argument("--holdout", type=float, default=None,
                   help="chronological holdout fraction, e.g. 0.3")
    a = p.parse_args()

    async def _run():
        if a.symbols:
            symbols = tuple(a.symbols)
        elif a.index:
            async with httpx.AsyncClient(timeout=60) as client:
                universe = await composite.load_universe(client, INDEXES[a.index])
            symbols = tuple(m.symbol for m in universe)
        else:
            symbols = DEFAULT_BASKET
        if a.limit:
            symbols = symbols[:a.limit]
        required = None
        if a.required:
            required = frozenset(LONG_NAME.get(r, r) for r in a.required)
        await main(symbols, a.atr_multiple, a.range_, a.max_days, a.draws,
                   CostModel(spread_bps=a.spread_bps, slippage_bps=a.slippage_bps),
                   required=required, holdout=a.holdout)

    asyncio.run(_run())
