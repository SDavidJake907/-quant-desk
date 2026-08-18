"""Exit rules judged by the resource that is actually scarce: a position slot.

    python scripts/momentum_portfolio.py --index sp500 --limit 250 --slots 5

WHY THIS EXISTS
Per-trade R answers "what does one trade earn" and assumes you could have taken
every signal. Nobody can. With five slots and a hundred signals a month, the
binding constraint is not which trade is best but which trade is FREE to take,
and a rule that returns less per trade while turning the slot over four times
as often can earn strictly more over a year.

The ladder tables measure the first question. This measures the second, and the
two can disagree -- which is the entire reason a take-profit exists.

HOW THE SIMULATION WORKS
Signals are merged into one chronological stream across all symbols. A signal
is taken if a slot is free on its date, and skipped otherwise. First come,
first served: ranking the queue by score would need a decision about signals
that arrive on the same day and would quietly import a second strategy.

WHAT IT STILL DOES NOT MODEL
Position sizing in dollars, so every slot is one unit of risk regardless of
price. Correlation, so five slots may all be one bet on the same sector. And
delisted names remain absent, so the whole result reads high.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from datetime import date

import httpx

from scanner.backtest.costs import CostModel
from scanner.backtest.ladder import ladder_outcome
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

#: (label, rungs, target_r, breakeven)
VARIANTS = (
    ("hold to 2R (baseline)", None, 2.0, False),
    ("hold, no target", None, None, False),
    ("1/2 at 1R, 1/2 at 1.5R", ((1.0, 0.5), (1.5, 0.5)), None, False),
    ("1/2 at 1.5R, rest runs", ((1.5, 0.5),), None, False),
    ("1/2 at 1.5R, runs, +BE", ((1.5, 0.5),), None, True),
    ("1/2 at 1R, runs, +BE", ((1.0, 0.5),), None, True),
    ("1/2 at 1R, rest runs", ((1.0, 0.5),), None, False),
    ("1/2 at 2R, rest runs", ((2.0, 0.5),), None, False),
    ("all out at 1R", ((1.0, 1.0),), None, False),
    ("all out at 1.5R", ((1.5, 1.0),), None, False),
)


def _leg_cost_r(entry: float, stop: float, costs: CostModel) -> float:
    stop_fraction = (entry - stop) / entry
    if stop_fraction <= 0:
        return 0.0
    return (costs.per_side_bps / 10_000.0) / stop_fraction


async def collect(symbols, range_):
    async with httpx.AsyncClient(timeout=60) as client:
        benchmark = await fetch_bars(BENCHMARK, client, range_=range_)
        results = await asyncio.gather(
            *(fetch_bars(s, client, range_=range_) for s in symbols),
            return_exceptions=True,
        )
    loaded = {s: r for s, r in zip(symbols, results) if not isinstance(r, Exception)}
    return loaded, benchmark, len(symbols) - len(loaded)


def simulate(trades: list[tuple[date, date, float]], slots: int) -> tuple[float, int, float]:
    """Run the slot queue over pre-scored trades.

    `trades` are (entry date, exit date, R), already chronological. Returns
    total R, trades actually taken, and mean sessions held.
    """
    free_from: list[date] = [date.min] * slots
    total_r, taken, held = 0.0, 0, []
    for entry_on, exit_on, r in trades:
        for slot in range(slots):
            if free_from[slot] <= entry_on:
                free_from[slot] = exit_on
                total_r += r
                taken += 1
                held.append((exit_on - entry_on).days)
                break
    return total_r, taken, (statistics.fmean(held) if held else 0.0)


async def main(symbols, atr_multiple, range_, max_days, slots, costs):
    print(f"{DIM}fetching {len(symbols)} symbols + {BENCHMARK}, daily bars, {range_}{RESET}")
    bars_by_symbol, benchmark, missing = await collect(symbols, range_)
    if not bars_by_symbol:
        raise SystemExit("no price history loaded")
    print(f"{DIM}loaded {len(bars_by_symbol)} symbols ({missing} unavailable); "
          f"{slots} slots; stop {atr_multiple:g} x ATR; time limit {max_days} sessions{RESET}")

    # Score every signal once under every variant, keeping calendar dates so
    # the queue can be run across symbols on one timeline.
    scored: dict[str, list[tuple[date, date, float]]] = {l: [] for l, _, _, _ in VARIANTS}
    signals = 0
    span: list[date] = []
    for symbol, bars in bars_by_symbol.items():
        setups = momentum.detect(bars, benchmark, atr_multiple=atr_multiple,
                                 reward_risk=2.0)
        for setup in setups:
            entry, stop = setup.entry_price, setup.stop_price
            risk = entry - stop
            leg = _leg_cost_r(entry, stop, costs)
            per_variant = []
            for label, rungs, target_r, be in VARIANTS:
                outcome = ladder_outcome(
                    bars, entry_index=setup.trigger_index, entry_price=entry,
                    stop_price=stop,
                    target_price=None if target_r is None else entry + target_r * risk,
                    rungs=rungs, max_bars=max_days, leg_cost_r=leg,
                    breakeven_after_first_rung=be,
                )
                if outcome is None:
                    per_variant = []
                    break
                per_variant.append((label, outcome))
            if not per_variant:
                continue
            signals += 1
            entry_on = bars.dates[setup.trigger_index]
            span.append(entry_on)
            for label, outcome in per_variant:
                scored[label].append((entry_on, bars.dates[outcome.exit_index],
                                      outcome.r_multiple))

    if not signals:
        raise SystemExit("no scoreable signals")
    years = max((max(span) - min(span)).days / 365.25, 1e-9)
    print(f"{DIM}{signals} signals over {years:.1f} years "
          f"({min(span)} to {max(span)}){RESET}\n")

    print(f"    {'variant':<30}{'taken':>7}{'of':>8}   {'R/trade':>8}  "
          f"{'total R':>9}  {'R/slot/yr':>10}  {'held':>6}")
    rows = []
    for label, _, _, _ in VARIANTS:
        trades = sorted(scored[label], key=lambda t: t[0])
        total_r, taken, held = simulate(trades, slots)
        per_trade = total_r / taken if taken else 0.0
        per_slot_year = total_r / slots / years
        rows.append((per_slot_year, label, taken, signals, per_trade, total_r, held))

    best = max(r[0] for r in rows)
    for per_slot_year, label, taken, of, per_trade, total_r, held in rows:
        colour = GREEN if per_slot_year == best else DIM
        print(f"    {label:<30}{taken:>7}{of:>8}   {per_trade:>+7.3f}R  "
              f"{total_r:>+8.1f}R  {colour}{per_slot_year:>+9.2f}R{RESET}  {held:>5.0f}d")

    print(f"\n{YELLOW}R/slot/yr is the column that answers the question.{RESET}")
    print(f"{DIM}  - R/trade can favour a rule that this column rejects: holding for a{RESET}")
    print(f"{DIM}    big winner blocks every signal that arrives meanwhile{RESET}")
    print(f"{DIM}  - 'taken of' shows how much of the opportunity set each rule reached{RESET}")
    print(f"{DIM}  - first come, first served; no ranking of same-day signals{RESET}")
    print(f"{DIM}  - every slot is one unit of risk; correlation is not modelled{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--index", choices=list(INDEXES), default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--atr-multiple", type=float, default=2.0)
    p.add_argument("--range", dest="range_", default="10y")
    p.add_argument("--max-days", type=int, default=60)
    p.add_argument("--slots", type=int, default=5)
    p.add_argument("--spread-bps", type=float, default=5.0)
    p.add_argument("--slippage-bps", type=float, default=2.0)
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
        await main(symbols, a.atr_multiple, a.range_, a.max_days, a.slots,
                   CostModel(spread_bps=a.spread_bps, slippage_bps=a.slippage_bps))

    asyncio.run(_run())
