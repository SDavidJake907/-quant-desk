"""Backtest the screens:

    python scripts/backtest.py --screen trend --horizon 21
    python scripts/backtest.py --screen coiled --horizon 42 --top 10
    python scripts/backtest.py --screen random     # the control
    python scripts/backtest.py --no-point-in-time  # the old, flattered way

The random control is the most important switch here. If a screen cannot beat
randomly picking from the same universe, it has no edge — it is just a
complicated way of holding stocks.

The universe is point-in-time by default: at each rebalance the screen only
sees companies the index actually held that day, reconstructed from the S&P
change log. Running with --no-point-in-time is useful for exactly one thing —
measuring how much of a result was survivorship bias.
"""

from __future__ import annotations

import argparse
import asyncio
import random
from datetime import date

import httpx

from scanner.backtest import engine
from scanner.features.bars import fetch_bars, fetch_many, range_start
from scanner.features.technicals import Features
from scanner.screens import coiled, momentum_factor, trend
from scanner.screens.trend import Candidate
from scanner.sources import index_history
from scanner.sources.constituents import load

BENCHMARK = "^GSPC"
BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"


def _random_screen(f: Features, rs: float | None) -> Candidate:
    """Control: passes everything, scores at random."""
    return Candidate(
        symbol=f.symbol, features=f, relative_strength=rs,
        criteria=(), score=random.random(),
    )


SCREENS = {"momentum": momentum_factor.evaluate, "trend": trend.evaluate,
           "coiled": coiled.evaluate, "random": _random_screen}


def _report(name: str, result: engine.BacktestResult) -> None:
    colour = GREEN if result.mean_excess > 0 else RED
    print(f"\n{BOLD}{name.upper()}{RESET}  "
          f"{result.rebalances} rebalances, {result.count} trades, "
          f"{result.horizon_days}d hold, top {result.top_n}")

    if not result.count:
        print(f"  {YELLOW}no trades — screen never triggered{RESET}")
        return

    print(f"  mean excess vs benchmark   {colour}{result.mean_excess:+.2f}%{RESET}")
    print(f"  median excess              {result.median_excess:+.2f}%")
    print(f"  trade hit rate             {result.hit_rate:.1f}%")
    print(f"  period hit rate            {result.period_hit_rate:.1f}%")
    print(f"  {DIM}mean raw return            {result.mean_return:+.2f}%{RESET}")
    print(f"  {DIM}mean benchmark return      {result.mean_benchmark:+.2f}%{RESET}")
    print(f"  {DIM}stdev of excess            {result.stdev_excess:.2f}%{RESET}")
    print(f"  {DIM}best / worst trade         {result.best:+.1f}% / {result.worst:+.1f}%{RESET}")

    verdict = (
        f"{GREEN}significant{RESET}" if abs(result.clustered_t) > 2.0
        else f"{YELLOW}NOT significant{RESET}"
    )
    print(f"  {BOLD}t-stat (clustered, n={result.effective_n}) "
          f"{result.clustered_t:+.2f}{RESET}  -> {verdict}")
    print(f"  {DIM}t-stat (naive, n={result.count}) {result.naive_t:+.2f} "
          f"<- inflated, ignore{RESET}")


def _memoized_membership(history: index_history.IndexHistory):
    """Membership lookups repeat across screens; the reconstruction is a walk
    over 400 changes, so remember what it produced."""
    cache: dict[date, frozenset[str]] = {}

    def members_on(on: date) -> frozenset[str]:
        if on not in cache:
            cache[on] = history.members_on(on, collapse_share_classes=True)
        return cache[on]

    return members_on


async def _point_in_time_universe(
    client: httpx.AsyncClient, range_: str
) -> tuple[tuple[str, ...], index_history.IndexHistory]:
    history = await index_history.load(client)
    start = range_start(range_)
    symbols = history.symbols_ever(start, date.today(), collapse_share_classes=True)
    current = len(history.members_on(date.today(), collapse_share_classes=True))
    print(f"{DIM}point-in-time universe: {len(symbols)} symbols ever in the index "
          f"since {start} vs {current} today{RESET}")
    print(f"{DIM}change log covers {history.oldest_change} -> "
          f"{history.newest_change} ({len(history.changes)} changes){RESET}")
    return tuple(sorted(symbols)), history


async def main(
    screen_name: str, horizon: int, step: int, top: int, limit: int,
    range_: str, point_in_time: bool,
) -> None:
    random.seed(42)  # reproducible control

    history: index_history.IndexHistory | None = None
    async with httpx.AsyncClient() as client:
        if point_in_time:
            symbols, history = await _point_in_time_universe(client, range_)
        else:
            universe = await load(client)
            symbols = tuple(c.symbol for c in universe)
            print(f"{YELLOW}today's membership only — survivorship bias is ON"
                  f"{RESET} {DIM}({len(symbols)} symbols){RESET}")
        if limit:
            symbols = symbols[:limit]

        benchmark = await fetch_bars(BENCHMARK, client, range_=range_)
        bars, errors = await fetch_many(symbols, client, range_=range_)

    print(f"{DIM}loaded {len(bars)} symbols ({len(errors)} unavailable), "
          f"benchmark has {len(benchmark)} bars{RESET}")

    membership = None
    if history is not None:
        bars, recycled = engine.drop_recycled(
            bars, history.exit_dates(range_start(range_), date.today())
        )
        if recycled:
            print(f"{YELLOW}dropped {len(recycled)} recycled tickers{RESET} "
                  f"{DIM}(price history begins after the company left the index): "
                  f"{', '.join(recycled)}{RESET}")
        membership = _memoized_membership(history)

    names = [screen_name] if screen_name != "all" else ["trend", "coiled", "random"]
    for name in names:
        result = engine.run(
            bars, benchmark, SCREENS[name],
            horizon_days=horizon, step_days=step, top_n=top,
            require_all=(name != "random"),
            membership=membership,
        )
        _report(name, result)
        if result.missing_members:
            _report_coverage(result)

    print(f"\n{YELLOW}Read before believing any of the above:{RESET}")
    if point_in_time:
        print(f"{DIM}  - membership is point-in-time, but delisted names still have"
              f" no price history, so deletions remain under-represented{RESET}")
    else:
        print(f"{DIM}  - survivorship bias: universe is TODAY's index membership,"
              f" so collapsed names are missing entirely{RESET}")
    print(f"{DIM}  - no commission, spread, slippage or market impact{RESET}")
    print(f"{DIM}  - overlapping windows make naive significance tests overconfident{RESET}")
    print(f"{DIM}  - one market regime; says nothing about a different one{RESET}")


def _report_coverage(result: engine.BacktestResult) -> None:
    """Deletions skew toward failures, so missing names are not random noise."""
    missing = result.missing_members
    print(f"  {YELLOW}{len(missing)} index members had no price history{RESET} "
          f"{DIM}(mean {result.mean_universe:.0f} screened per rebalance){RESET}")
    print(f"  {DIM}{', '.join(missing[:12])}"
          f"{' …' if len(missing) > 12 else ''}{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--screen", default="all", choices=[*SCREENS, "all"])
    p.add_argument("--horizon", type=int, default=21, help="holding period in bars")
    p.add_argument("--step", type=int, default=21, help="days between rebalances")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--range", dest="range_", default="5y")
    p.add_argument("--no-point-in-time", dest="point_in_time", action="store_false",
                   help="use today's index membership (flattered; for comparison)")
    a = p.parse_args()
    asyncio.run(main(a.screen, a.horizon, a.step, a.top, a.limit, a.range_,
                     a.point_in_time))
