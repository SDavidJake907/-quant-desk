"""Snapshot today's intraday bars into the accumulating store.

    python scripts/collect_intraday.py                     # S&P 500, 30m
    python scripts/collect_intraday.py --index all         # all 1,504
    python scripts/collect_intraday.py --minutes 15

Run it once per trading day, after the close. Yahoo shows only the last 86
days of sub-hourly data and that window rolls, so a 30-minute backtest starts
at 86 days and stays there unless something keeps what it was shown. After a
year of daily runs the same backtest has four times the sample.

Safe to run repeatedly: bars still forming are never written, and a re-fetch
overwrites what it overlaps, so five runs a day equal one.
"""

from __future__ import annotations

import argparse
import asyncio

import httpx

from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED, fetch_intraday, max_range
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

DIM, BOLD, RESET, YELLOW = "\033[2m", "\033[1m", "\033[0m", "\033[33m"
INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}


async def main(
    symbols: tuple[str, ...], minutes: int, alignment: str, batch_size: int
) -> None:
    range_ = max_range(minutes)
    print(f"{DIM}collecting {len(symbols)} symbols at {minutes}m ({range_} available){RESET}")

    stored = grown = failed = 0
    total_bars = 0
    deepest = ("", 0)
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    async with httpx.AsyncClient() as client:
        for start in range(0, len(symbols), batch_size):
            batch = symbols[start : start + batch_size]
            results = await asyncio.gather(
                *(
                    fetch_intraday(s, client, minutes=minutes, range_=range_,
                                   alignment=alignment)
                    for s in batch
                ),
                return_exceptions=True,
            )
            for symbol, outcome in zip(batch, results):
                if isinstance(outcome, Exception):
                    failed += 1
                    continue
                before = len(history.load(symbol, minutes, alignment))
                after = history.collect(outcome)
                stored += 1
                total_bars += after
                if after > before:
                    grown += 1
                if after > deepest[1]:
                    deepest = (symbol, after)

    print(f"{BOLD}{stored} symbols stored{RESET} "
          f"{DIM}({grown} gained bars, {failed} unavailable){RESET}")
    if stored:
        print(f"{DIM}{total_bars:,} bars held in total, "
              f"deepest series {deepest[0]} at {deepest[1]:,}{RESET}")
    print(f"{DIM}store: {history.STORE_DIR}{RESET}")
    print(f"\n{YELLOW}Run this daily. The store only grows while you feed it,{RESET}")
    print(f"{DIM}and the days it misses are gone for good.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--index", choices=list(INDEXES), default="sp500")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--minutes", type=int, default=30)
    p.add_argument("--alignment", default=SESSION_ALIGNED)
    p.add_argument("--batch-size", type=int, default=100,
                   help="symbols fetched at once; lower this if memory is tight")
    a = p.parse_args()

    async def _symbols() -> tuple[str, ...]:
        if a.symbols:
            return tuple(s.upper() for s in a.symbols)
        async with httpx.AsyncClient() as client:
            universe = await composite.load_universe(client, INDEXES[a.index])
        chosen = tuple(m.symbol for m in universe)
        return chosen[: a.limit] if a.limit else chosen

    asyncio.run(main(asyncio.run(_symbols()), a.minutes, a.alignment, a.batch_size))
