"""The four Alpaca datasets this project was not collecting.

    python scripts/collect_alpaca_extras.py --news --start 2024-09-01
    python scripts/collect_alpaca_extras.py --auctions --start 2024-09-01
    python scripts/collect_alpaca_extras.py --news --auctions

WHAT WAS MISSING, AND WHY IT MATTERED
Bars were collected three ways -- regular, extended, 15-minute. Four other
datasets were available on this tier the whole time and none were taken:

  news      back to 2015   makes the model layer testable for the first time
  auctions  back to 2020   the official open, which the open-sweep model needs
  trades    back to 2020   the fill-rate assumption, never measured
  quotes    back to 2020   spread, the best missing constraint

NEWS IS THE ONE THAT CHANGES WHAT IS POSSIBLE
`ranking.py` has never been scored because the news store held two days: asking
the model about a 2025 setup gave it either no news or TODAY's news, and the
second is lookahead that would manufacture a spectacular result. Alpaca serves
timestamped news back to 2015 and the setups span 2024-2026, so every historical
candidate can now be given the news that existed at the time.

The remaining caveat is real and is not solved by this: the model's WEIGHTS
already know what happened to these companies. "Detecting Lookahead Bias in LLM
Forecasts" proposes anonymising names and dates in the prompt, and that has to
be in place before any number from an LLM backtest means anything.

WHY ONLY TWO OF THE FOUR ARE COLLECTED IN BULK
News and auctions are small: tens of items per symbol-year, and two prints per
symbol-day. Quotes and trades are not -- millions and hundreds of thousands per
symbol-DAY respectively. Pulling those across 501 symbols and two years would be
terabytes to answer two specific questions.

So they are deliberately left for targeted collection: fetch trades only in the
minutes after a signal bar to ask whether a limit at the trigger close would
actually have filled, and quotes only at trigger times to measure the spread
paid. Bulk-collecting them would be gathering data for its own sake, which is
the opposite of the reason the other two are worth having.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

DATA = "https://data.alpaca.markets"
INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}

#: Point-in-time news, keyed by the day it was PUBLISHED. Deliberately separate
#: from `data/news`, which holds what the live scan saw: one is history and the
#: other is a record of what this system actually knew at decision time, and
#: conflating them would let hindsight into the live audit trail.
NEWS_ROOT = Path("data/news_history")
#: One file per symbol: opening and closing auction prints.
AUCTION_ROOT = Path("data/auctions")


def _headers() -> dict[str, str]:
    return {"APCA-API-KEY-ID": settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": settings.alpaca_secret_key}


async def _paged(client: httpx.AsyncClient, url: str, params: dict,
                 key: str, cap: int = 40) -> list:
    """Follow next_page_token, bounded so one symbol cannot run forever."""
    out: list = []
    token = None
    for _ in range(cap):
        query = dict(params)
        if token:
            query["page_token"] = token
        try:
            response = await client.get(url, headers=_headers(), params=query,
                                        timeout=90)
        except httpx.HTTPError:
            break
        if response.status_code == 429:
            await asyncio.sleep(2.0)
            continue
        if response.status_code != 200:
            break
        payload = response.json()
        chunk = payload.get(key)
        if isinstance(chunk, dict):
            for rows in chunk.values():
                out.extend(rows)
        elif isinstance(chunk, list):
            out.extend(chunk)
        token = payload.get("next_page_token")
        if not token:
            break
    return out


async def collect_news(client: httpx.AsyncClient, symbols: list[str],
                       start: str) -> int:
    """Point-in-time news, written one JSONL file per PUBLICATION day."""
    NEWS_ROOT.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[dict]] = {}
    seen: set[str] = set()
    done = 0
    for offset in range(0, len(symbols), 20):
        batch = symbols[offset:offset + 20]
        items = await _paged(client, f"{DATA}/v1beta1/news", {
            "symbols": ",".join(batch), "start": start, "limit": 50,
            "sort": "asc"}, "news")
        for item in items:
            ident = str(item.get("id", ""))
            if not ident or ident in seen:
                continue
            seen.add(ident)
            stamp = str(item.get("created_at", ""))[:10]
            if not stamp:
                continue
            by_day.setdefault(stamp, []).append({
                "id": ident, "created_at": item.get("created_at"),
                "headline": item.get("headline", ""),
                "summary": item.get("summary", ""),
                "source": item.get("source", ""),
                "symbols": item.get("symbols", []),
            })
        done += len(batch)
        if done % 100 == 0 or done >= len(symbols):
            print(f"{DIM}  news {done}/{len(symbols)} symbols, "
                  f"{len(seen)} unique items{RESET}")
        await asyncio.sleep(0.25)
    for day, rows in sorted(by_day.items()):
        path = NEWS_ROOT / f"{day}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
    print(f"{GREEN}{len(seen)}{RESET} news items across "
          f"{len(by_day)} days -> {NEWS_ROOT}")
    return len(seen)


async def collect_auctions(client: httpx.AsyncClient, symbols: list[str],
                           start: str) -> int:
    """Opening and closing auction prints, one file per symbol.

    The opening auction is the official open -- the exact price at which the
    overnight range resolves. The 30m store's first bar spans 09:30-10:00 and
    cannot separate the auction from the half hour that follows it.
    """
    AUCTION_ROOT.mkdir(parents=True, exist_ok=True)
    total = 0
    for index, symbol in enumerate(symbols, 1):
        rows = await _paged(client, f"{DATA}/v2/stocks/{symbol}/auctions",
                            {"start": start, "limit": 10000}, "auctions")
        if rows:
            (AUCTION_ROOT / f"{symbol}.json").write_text(
                json.dumps(rows), encoding="utf-8")
            total += len(rows)
        if index % 100 == 0 or index == len(symbols):
            print(f"{DIM}  auctions {index}/{len(symbols)}, "
                  f"{total:,} prints{RESET}")
        await asyncio.sleep(0.2)
    print(f"{GREEN}{total:,}{RESET} auction prints -> {AUCTION_ROOT}")
    return total


async def main(index: str, start: str, limit: int,
               news: bool, auctions: bool) -> int:
    if not settings.alpaca_key_id:
        print(f"{RED}Alpaca keys are not configured{RESET}")
        return 2
    if not (news or auctions):
        print(f"{YELLOW}nothing selected — pass --news and/or --auctions"
              f"{RESET}")
        return 1
    began = time.perf_counter()
    async with httpx.AsyncClient() as client:
        universe = await composite.load_universe(client, INDEXES[index])
        symbols = [m.symbol for m in universe][:limit or None]
        print(f"{DIM}{len(symbols)} symbol(s) from {start}{RESET}")
        if news:
            await collect_news(client, symbols, start)
        if auctions:
            await collect_auctions(client, symbols, start)
    print(f"{DIM}{time.perf_counter() - began:.0f}s{RESET}")
    print(f"{DIM}Trades and quotes are NOT collected in bulk on purpose — "
          f"millions of rows per symbol-day to answer two specific questions. "
          f"Fetch them per setup when measuring fills or spread.{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    parser.add_argument("--start", default="2024-09-01")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--news", action="store_true")
    parser.add_argument("--auctions", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(
        main(args.index, args.start, args.limit, args.news, args.auctions)))
