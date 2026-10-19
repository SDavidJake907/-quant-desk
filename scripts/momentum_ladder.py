"""Take-profit ladders and stop widths for the momentum swing setup.

    python scripts/momentum_ladder.py --index sp500 --limit 250
    python scripts/momentum_ladder.py --atr-multiple 1 1.5 2 3 4 --holdout 0.3

TWO QUESTIONS, ASKED TOGETHER BECAUSE THEY ARE NOT SEPARABLE
Where the stop goes decides what 1R means, and every rung is priced in R. Move
the stop and every rung moves with it, so tuning them one at a time measures
neither. The grid below varies both.

THE COMPARISON THAT MATTERS
momentum_backtest.py established that this screen loses to random entries under
a fixed 2R target. A ladder that improves the pattern proves nothing on its
own: laddering may simply improve any trade. So every variant is run on random
entries too, with the same stop distance, the same rungs and the same per-leg
cost. The screen earns its place only by beating its own control.

WHAT TO READ, AND WHAT TO IGNORE
Read mean R. A ladder reliably raises win rate and lowers variance while
earning less, so both of those columns will look encouraging for variants that
are quietly worse. The best column shows the right tail the expectancy is made
of, and a ladder that flattens it is taking away the reason to be here.
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

#: (label, rungs, target_r, breakeven). A target_r of None lets the remainder
#: run to the time limit -- the variant that stops capping the right tail.
#: `breakeven` raises the stop to the entry once the first rung fills.
LADDERS = (
    ("hold to 2R (baseline)", None, 2.0, False),
    ("hold, no target", None, None, False),
    # The four readings of "half at 1R / half at 1.5R, stop to the mark".
    ("1/2 at 1R, 1/2 at 1.5R", ((1.0, 0.5), (1.5, 0.5)), None, False),
    ("1/2 at 1.5R, rest runs", ((1.5, 0.5),), None, False),
    ("1/2 at 1.5R, runs, +BE", ((1.5, 0.5),), None, True),
    ("1/2 at 1R, runs, +BE", ((1.0, 0.5),), None, True),
    # Context for those, so a good number can be recognised as good.
    ("1/2 at 1R, rest runs", ((1.0, 0.5),), None, False),
    ("1/3 at 1R, 1/3 at 2R, runs", ((1.0, 1 / 3), (2.0, 1 / 3)), None, False),
    ("1/2 at 2R, rest runs", ((2.0, 0.5),), None, False),
    ("all out at 1R", ((1.0, 1.0),), None, False),
    # The operator's three steps (2026-10-19): a third each at 1R, 2R, 3R.
    ("1/3 at 1R, 2R, 3R", ((1.0, 1 / 3), (2.0, 1 / 3), (3.0, 1 / 3)), None, False),
    ("1/3 at 1R, 2R, 3R, +BE", ((1.0, 1 / 3), (2.0, 1 / 3), (3.0, 1 / 3)), None, True),
)


def _stats(rs):
    if len(rs) < 2:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    mean = statistics.fmean(rs)
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    win = sum(1 for r in rs if r > 0) / len(rs) * 100.0
    return mean, se, (mean / se if se else 0.0), win, max(rs)


def _row(label, real, control):
    mean, se, t, win, best = _stats(real)
    c_mean, _, _, _, _ = _stats(control)
    edge = mean - c_mean
    colour = GREEN if edge > 0 else RED
    print(f"    {label:<30}{len(real):>6} {mean:>+8.3f}R  se {se:>5.3f}  t {t:>+5.2f}"
          f"  win {win:>5.1f}%  best {best:>+7.2f}R"
          f"   random {c_mean:>+7.3f}R  {colour}edge {edge:>+7.3f}R{RESET}")


async def collect(symbols, range_):
    async with httpx.AsyncClient(timeout=60) as client:
        benchmark = await fetch_bars(BENCHMARK, client, range_=range_)
        results = await asyncio.gather(
            *(fetch_bars(s, client, range_=range_) for s in symbols),
            return_exceptions=True,
        )
    loaded = {s: r for s, r in zip(symbols, results) if not isinstance(r, Exception)}
    return loaded, benchmark, len(symbols) - len(loaded)


def _leg_cost_r(entry: float, stop: float, costs: CostModel) -> float:
    """Spread crossing on one exit leg, expressed in R.

    Friction is a fraction of PRICE; R is a fraction of the stop distance. So
    the cost of a leg in R is friction divided by stop width -- three times
    heavier on a 1.5% stop than on a 5% one. One hardcoded constant shared
    across strategies with different stop widths mis-prices all but one.
    """
    stop_fraction = (entry - stop) / entry
    if stop_fraction <= 0:
        return 0.0
    return (costs.per_side_bps / 10_000.0) / stop_fraction


def _score(bars, entry_index, entry, stop, rungs, target_r, breakeven,
           max_days, costs):
    risk = entry - stop
    target = None if target_r is None else entry + target_r * risk
    return ladder_r(
        bars, entry_index=entry_index, entry_price=entry, stop_price=stop,
        target_price=target, rungs=rungs, max_bars=max_days,
        leg_cost_r=_leg_cost_r(entry, stop, costs),
        breakeven_after_first_rung=breakeven,
    )


async def main(symbols, atr_multiples, range_, max_days, draws, costs, holdout):
    print(f"{DIM}fetching {len(symbols)} symbols + {BENCHMARK}, daily bars, {range_}{RESET}")
    bars_by_symbol, benchmark, missing = await collect(symbols, range_)
    if not bars_by_symbol:
        raise SystemExit("no price history loaded")
    print(f"{DIM}loaded {len(bars_by_symbol)} symbols ({missing} unavailable); "
          f"time limit {max_days} sessions; {costs.round_trip_bps:.1f}bps round trip; "
          f"{draws} random draws per signal{RESET}")

    for atr_multiple in atr_multiples:
        rows = []
        widths = []
        for symbol, bars in bars_by_symbol.items():
            setups = momentum.detect(bars, benchmark, atr_multiple=atr_multiple,
                                     reward_risk=2.0)
            low, high = momentum.MIN_HISTORY - 1, len(bars) - 1
            for setup in setups:
                entry, stop = setup.entry_price, setup.stop_price
                stop_fraction = (entry - stop) / entry
                real = {
                    label: _score(bars, setup.trigger_index, entry, stop,
                                  rungs, target_r, be, max_days, costs)
                    for label, rungs, target_r, be in LADDERS
                }
                if any(v is None for v in real.values()):
                    continue

                # Random entries in the SAME name, same stop width, same rungs.
                # Seeded per signal so the control is reproducible.
                rng = random.Random(setup.trigger_index)
                picks = ([rng.randrange(low, high) for _ in range(draws)]
                         if high > low else [])
                control = {label: [] for label, _, _, _ in LADDERS}
                for label, rungs, target_r, be in LADDERS:
                    for i in picks:
                        c_entry = bars.closes[i]
                        c_stop = c_entry * (1 - stop_fraction)
                        r = _score(bars, i, c_entry, c_stop, rungs, target_r,
                                   be, max_days, costs)
                        if r is not None:
                            control[label].append(r)

                widths.append(stop_fraction * 100.0)
                rows.append((setup.trigger_index / len(bars), real, control))

        if not rows:
            print(f"\n{YELLOW}ATR x {atr_multiple:g}: no scoreable signals{RESET}")
            continue

        rows.sort(key=lambda r: r[0])
        periods = (("full", rows),)
        if holdout is not None:
            cut = int(len(rows) * (1 - holdout))
            periods = (("train", rows[:cut]), ("holdout", rows[cut:]))

        mean_width = statistics.fmean(widths) if widths else 0.0
        for name, part in periods:
            print(f"\n{BOLD}ATR x {atr_multiple:g} -- {name}{RESET} "
                  f"{DIM}({len(part)} signals, mean stop {mean_width:.1f}% of price){RESET}")
            for label, _, _, _ in LADDERS:
                _row(label,
                     [r[label] for _, r, _ in part],
                     [v for _, _, c in part for v in c[label]])

    print(f"\n{YELLOW}Reading this table:{RESET}")
    print(f"{DIM}  - edge is pattern minus its OWN random control; positive is the{RESET}")
    print(f"{DIM}    only result that says the screen contributed anything{RESET}")
    print(f"{DIM}  - win rate rises and variance falls on ladders that earn less{RESET}")
    print(f"{DIM}  - many variants are read at once; the best of N is biased upward,{RESET}")
    print(f"{DIM}    which is what the holdout column exists to catch{RESET}")
    print(f"{DIM}  - delisted names are absent, so every long result reads high{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--index", choices=list(INDEXES), default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--atr-multiple", type=float, nargs="+", default=[2.0])
    p.add_argument("--range", dest="range_", default="10y")
    p.add_argument("--max-days", type=int, default=60)
    p.add_argument("--draws", type=int, default=3)
    p.add_argument("--spread-bps", type=float, default=5.0)
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--holdout", type=float, default=None)
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
        await main(symbols, tuple(a.atr_multiple), a.range_, a.max_days, a.draws,
                   CostModel(spread_bps=a.spread_bps, slippage_bps=a.slippage_bps),
                   a.holdout)

    asyncio.run(_run())
