"""Are any trading days missing from the bar data?

    python scripts/data_check.py                 # S&P 500, daily bars
    python scripts/data_check.py --limit 120     # faster spot check
    python scripts/data_check.py --intraday      # the live scanner's feed

READ-ONLY, and outside the frozen trading path. It fetches bars and reports.

WHY IT EXISTS
On 2026-08-18 every one of 120 S&P 500 symbols had a NULL close for Monday
2026-08-17 in Yahoo's daily series. The bar existed; its close was None. The
parser skips those rather than invent a price — correct — and it does so in
silence, which is not. A whole trading day left every daily series at once and
nothing said so.

Intraday bars were unaffected, so the live scanner ran normally and looked
healthy. Only daily-bar research was wrong, and only invisibly. That is the
same failure as the adjusted-price bug: not wrong arithmetic, just data quietly
gone while every number still prints.

TWO THINGS ARE COUNTED, BECAUSE THEY MEAN DIFFERENT THINGS
A NULL close is the provider handing back a bar with no price — the actual
defect. A GAP is a day one symbol lacks that its neighbours have, which is what
the null becomes after parsing. Reporting both says whether the data is arriving
broken or is being dropped somewhere later.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone

import httpx

from scanner.execution.policy import TradingPolicy
from scanner.features.bars import fetch_payload
from scanner.features.coverage import find_gaps
from scanner.features.intraday import fetch_intraday, max_range
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}
P = TradingPolicy()

#: Days back to examine. Older data is not going to be repaired.
RECENT_DAYS = 30


async def _daily(symbols, client):
    """(dates seen, null-close count by date) per symbol, from raw payloads."""
    results = await asyncio.gather(
        *(fetch_payload(s, client, range_="1mo", interval="1d", ttl_seconds=1)
          for s in symbols),
        return_exceptions=True)
    series, nulls, fetched = {}, Counter(), Counter()
    for symbol, payload in zip(symbols, results):
        if isinstance(payload, Exception):
            continue
        try:
            result = payload["chart"]["result"][0]
            stamps = result["timestamp"]
            closes = result["indicators"]["quote"][0].get("close") or []
        except (KeyError, IndexError, TypeError):
            continue
        days = []
        for stamp, close in zip(stamps, closes):
            day = datetime.fromtimestamp(stamp, timezone.utc).date()
            fetched[day] += 1
            if close is None:
                nulls[day] += 1
            else:
                days.append(day)
        series[symbol] = days
    return series, nulls, fetched


async def _intraday(symbols, client):
    """Session dates present in the live scanner's own feed."""
    results = await asyncio.gather(
        *(fetch_intraday(s, client, minutes=P.interval_minutes,
                         range_=max_range(P.interval_minutes))
          for s in symbols),
        return_exceptions=True)
    series = {}
    for symbol, bars in zip(symbols, results):
        if isinstance(bars, Exception):
            continue
        series[symbol] = sorted({t.date() for t in bars.times})
    return series, Counter(), Counter()


async def main(index: str, limit: int, intraday: bool) -> int:
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])
        symbols = [m.symbol for m in universe][:limit or None]
        feed = "intraday 30m" if intraday else "daily"
        print(f"{DIM}checking {len(symbols)} symbols, {feed} bars{RESET}")
        loader = _intraday if intraday else _daily
        series, nulls, fetched = await loader(symbols, client)

    if not series:
        print(f"{RED}no data returned at all{RESET}")
        return 1
    print(f"{DIM}{len(series)} symbols returned data{RESET}")

    problems = 0

    if nulls:
        print(f"\n{BOLD}NULL CLOSES{RESET} "
              f"{DIM}(the provider returned a bar with no price){RESET}")
        for day in sorted(nulls, reverse=True)[:RECENT_DAYS]:
            count, total = nulls[day], fetched[day]
            share = count / total * 100 if total else 0
            tint = RED if share > 50 else YELLOW
            print(f"  {tint}{day}  {count:>4} of {total:<4} ({share:>3.0f}%){RESET}")
            problems += 1

    gaps = find_gaps(series)
    if gaps:
        print(f"\n{BOLD}MISSING DAYS{RESET} "
              f"{DIM}(a day some symbols have and others do not){RESET}")
        for gap in gaps[:RECENT_DAYS]:
            tint = RED if gap.is_provider_wide else YELLOW
            print(f"  {tint}{gap.describe()}{RESET}")
            problems += 1

    if not problems:
        print(f"\n{GREEN}no gaps and no null closes{RESET}")
        return 0

    print(f"\n{YELLOW}What this means:{RESET}")
    print(f"{DIM}  - PROVIDER-WIDE days are missing for everyone. Any research{RESET}")
    print(f"{DIM}    over that period is computed without them and will not say so{RESET}")
    print(f"{DIM}  - single-symbol gaps usually clear on a refetch{RESET}")
    print(f"{DIM}  - the live scanner reads INTRADAY bars, so a daily gap does{RESET}")
    print(f"{DIM}    not affect trading — run with --intraday to check that feed{RESET}")
    return 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--intraday", action="store_true",
                   help="check the 30m feed the live scanner actually uses")
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.index, a.limit, a.intraday)))
