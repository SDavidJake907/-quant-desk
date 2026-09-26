"""Keep the news the market makes while nothing is trading.

    python scripts/collect_news.py                 # the liquid universe
    python scripts/collect_news.py --symbols PFE KO
    python scripts/collect_news.py --limit 60      # faster spot check

READ-ONLY of the market, append-only to disk. It fetches headlines and stores
them. It places no order, reads no account and touches no policy.

WHY A WEEKEND JOB EXISTS AT ALL
The scanner already runs seven days a week, and on a weekend it gathers
nothing: no bars print, so Saturday re-reads Friday's series, finds the same
setups, ages them all past the freshness window, and produces zero candidates.
Measured across 28 weekend scans: zero candidates, every time.

News is the one thing that does not stop. Earnings pre-announcements, merger
agreements and guidance land disproportionately on Friday night and over the
weekend, and until now all of it was discarded — `news_store` is only reached
from inside `if news_gate and candidates:`, and on a weekend there are never
any candidates.

WHAT THIS IS NOT
It is not decision provenance. `news_store` written from the scan holds "the
headlines a decision was shown", which is what a replay of a prompt change
needs. This writes a broader corpus: what was PUBLISHED, whether or not
anything looked at it. Both are point-in-time and both are useful, and they
answer different questions — so the difference is recorded here rather than
left for someone to infer from a mixed file later.

WHAT IT IS FOR
Scoring the news gate against real flow. The gate refuses trades — two on
structural news so far, and CCL on earnings — and nobody has ever checked
whether those refusals were right. That question needs months of headlines
that nobody curated, and the archive has to start before the question can be
asked.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.execution.policy import TradingPolicy
from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED
from scanner.sources import composite, news_store, yahoo_rss
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}
POLICY = TradingPolicy()
MIN_PRICE = 5.0

#: Fetch this many feeds at once. Yahoo tolerated 1,500 bar requests in ~40s,
#: but RSS is a different endpoint and there is no reason to find its limit
#: on a job that has all weekend to finish.
BATCH = 20


def _dollar_volume(bars, window: int = 20) -> float:
    closes, volumes = bars.closes[-window:], bars.volumes[-window:]
    if not closes:
        return 0.0
    return sum(v * c for v, c in zip(volumes, closes)) / len(closes)


def liquid_universe(symbols: list[str], minutes: int) -> list[str]:
    """Only names the scanner could actually trade.

    Storing news for a symbol the liquidity floor removes is storing text
    about a trade that can never happen. 245 of 501 S&P 500 names pass; of
    1,003 mid and small caps, 18 do.
    """
    keep = []
    for symbol in symbols:
        bars = history.accumulated(symbol, minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 20:
            continue
        if bars.closes[-1] < MIN_PRICE:
            continue
        if _dollar_volume(bars) < POLICY.min_dollar_volume:
            continue
        keep.append(symbol)
    return keep


async def main(index: str, symbols: tuple[str, ...], limit: int,
               minutes: int) -> int:
    async with httpx.AsyncClient(timeout=30) as client:
        if symbols:
            wanted = [s.upper() for s in symbols]
        else:
            universe = await composite.load_universe(client, INDEXES[index])
            wanted = liquid_universe([m.symbol for m in universe], minutes)
            print(f"{DIM}{len(wanted)} liquid name(s) of "
                  f"{len(universe)} in {index}{RESET}")
        if limit:
            wanted = wanted[:limit]

        stored = fetched = failed = 0
        started = datetime.now()
        for start in range(0, len(wanted), BATCH):
            batch = wanted[start:start + BATCH]
            results = await asyncio.gather(
                *(yahoo_rss.fetch(s, client) for s in batch),
                return_exceptions=True,
            )
            for symbol, result in zip(batch, results):
                if isinstance(result, Exception):
                    failed += 1
                    continue
                fetched += len(result)
                # Never raises: these headlines are evidence, and a full disk
                # must not take down a collector that has all weekend to run.
                stored += news_store.append_items(result)

    elapsed = (datetime.now() - started).total_seconds()
    print(f"{BOLD}{stored}{RESET} new headline(s) stored "
          f"{DIM}from {fetched} fetched across {len(wanted)} symbol(s) "
          f"in {elapsed:.0f}s{RESET}")
    if failed:
        print(f"{YELLOW}{failed} feed(s) failed — skipped, not fatal{RESET}")
    today = datetime.now().astimezone().date()
    print(f"{DIM}store: {news_store.day_path(today)}{RESET}")
    if not stored and fetched:
        print(f"{GREEN}nothing new — everything fetched was already "
              f"recorded today{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--symbols", nargs="*", default=[])
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--minutes", type=int, default=POLICY.interval_minutes)
    a = p.parse_args()
    raise SystemExit(asyncio.run(
        main(a.index, tuple(a.symbols), a.limit, a.minutes)))
