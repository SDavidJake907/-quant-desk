"""Pull 30-minute SIP bars from Alpaca into a store beside the Yahoo one.

    python scripts/collect_alpaca.py --start 2024-09-01        # ~2 years
    python scripts/collect_alpaca.py --start 2016-01-01        # everything
    python scripts/collect_alpaca.py --limit 40                # spot check

WHY THIS EXISTS
The strategy trades 30-minute bars and the Yahoo store holds 96 sessions —
0.38 years — because Yahoo caps sub-hourly history at 60 days and the store
only exists by accumulating past that cap. Every measurement here is limited
by that: 196 setups, a deflated Sharpe of 0.82 against 50 declared trials, and
thirteen ranking ideas that could not be settled either way.

Measured on this account's keys: Alpaca's FREE tier serves **SIP** 30-minute
bars back to **2016-01-01**, current to the last session. That is roughly
twenty-five times the history, from the venue the orders actually go to.

The README's reason for choosing Yahoo — "the free market-data tier is
IEX-only, which is fine for order routing and wrong for volume profiles" — was
correct and is now out of date. IEX measured 4.4% of SIP volume on AAPL and
7.5% on KEY, and only reaches back to 2020. SIP has neither problem.

A PARALLEL STORE, NOT A REPLACEMENT
Written to `data/intraday_alpaca` so `data/intraday` is untouched. Two reasons.

Every number this project has published came from Yahoo bars, so the honest
use of a second provider is REPLICATION: run the ledger again and see whether
the +0.218R baseline and the 20M liquidity floor survive on independent data.
Overwriting the first store would destroy the comparison that makes the second
one worth having.

And the volumes do not agree. Measured over the same sessions, Alpaca/Yahoo
was 0.87x on AAPL, 0.87x on KEY, 1.09x on CL and 1.49x on VRTX — not a
constant, so **the 20M floor does not transfer**. KEY reads 19.88M on Alpaca
and 22.73M on Yahoo: the same symbol on opposite sides of the line. The floor
is this project's strongest result at p=0.022 and it has to be re-measured on
Alpaca volume before it is trusted there.

RATE LIMITS
The free tier allows 200 requests a minute. Each symbol needs one request per
10,000 bars, so a decade is three or four. This paces itself and reports what
it did rather than racing the limit.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution.policy import TradingPolicy
from scanner.features import history
from scanner.features.intraday import EXCHANGE_TZ, SESSION_ALIGNED
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}
V = TradingPolicy()

BASE = "https://data.alpaca.markets/v2/stocks"
#: Alpaca's own floor for 30-minute SIP bars on this tier, measured.
EARLIEST = "2016-01-01"
#: 200/min allowed; 8 concurrent with a small pause stays well inside it.
CONCURRENCY = 8

STORE = Path("data/intraday_alpaca")


def _headers() -> dict[str, str]:
    return {"APCA-API-KEY-ID": settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": settings.alpaca_secret_key}


async def fetch_symbol(client: httpx.AsyncClient, symbol: str,
                       start: str, minutes: int = 30
                       ) -> list[list[float]] | str:
    """Every SIP bar for one symbol, paginated. Returns rows or an error.

    THE TIMEFRAME FOLLOWS `minutes`. It used to be hardcoded "30Min" while
    `--minutes` controlled only the SAVE, so `--minutes 15` fetched 30-minute
    bars and stored them labelled as 15-minute ones. Nothing downstream could
    have detected that: the file name, the bar count and the session grouping
    would all look plausible, and every number computed from it would be wrong.
    """
    rows: list[list[float]] = []
    token = None
    while True:
        params = {"timeframe": f"{minutes}Min", "start": start, "limit": 10000,
                  "feed": "sip", "adjustment": "all"}
        if token:
            params["page_token"] = token
        try:
            response = await client.get(f"{BASE}/{symbol}/bars",
                                        headers=_headers(), params=params,
                                        timeout=90)
        except httpx.HTTPError as exc:
            return f"http: {exc}"
        if response.status_code == 429:
            await asyncio.sleep(2.0)
            continue
        if response.status_code != 200:
            return f"HTTP {response.status_code}"
        payload = response.json()
        for bar in payload.get("bars") or []:
            # Alpaca stamps RFC3339 UTC; the store keeps epoch seconds and
            # `to_bars` renders them in exchange time, same as the Yahoo path.
            when = datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
            local = when.astimezone(EXCHANGE_TZ)

            # REGULAR HOURS ONLY.
            #
            # Alpaca SIP returns the full extended session, 04:00 to 19:30 —
            # 32 distinct bucket times against a regular session's 13, and
            # sessions of 14 to 19 bars. Measured: only 81% of returned bars
            # fall inside 09:30-15:30.
            #
            # Keeping them would not be "more data", it would be a different
            # instrument. Every structural rule here assumes 13-bar sessions
            # with a gap between them: `crosses_session`, `is_stale_session`,
            # `bars_until_weekend`, `session_slot`, the Friday flatten, and
            # the 60-bar hold limit of "about 4.6 sessions". Pre-market bars
            # also carry a fraction of the volume, which would drag the
            # dollar-volume average exactly the way the 16:00 placeholder did.
            #
            # The LAST bucket of a session STARTS at 16:00 minus the bar
            # width: 15:30 for 30m, 15:45 for 15m, 15:55 for 5m. Hardcoding
            # 15:30 here silently dropped the final bar of every session at
            # any interval finer than 30 minutes.
            last_start = 16 * 60 - minutes
            minute_of_day = local.hour * 60 + local.minute
            if not (9 * 60 + 30) <= minute_of_day <= last_start:
                continue

            rows.append([when.timestamp(), float(bar["o"]), float(bar["h"]),
                         float(bar["l"]), float(bar["c"]), float(bar["v"])])
        token = payload.get("next_page_token")
        if not token:
            break
    return rows


async def main(index: str, start: str, limit: int, minutes: int) -> int:
    if not settings.alpaca_key_id or not settings.alpaca_secret_key:
        print(f"{RED}Alpaca keys are not configured{RESET}")
        return 2

    async with httpx.AsyncClient() as client:
        universe = await composite.load_universe(client, INDEXES[index])
        symbols = [m.symbol for m in universe]
        if limit:
            symbols = symbols[:limit]

        print(f"{DIM}{len(symbols)} symbol(s), {minutes}m SIP bars from {start}"
              f"{RESET}")
        print(f"{DIM}store: {STORE}  (the Yahoo store is untouched){RESET}")

        stored = failed = 0
        depths: list[int] = []
        problems: list[str] = []
        began = time.perf_counter()
        original = history.STORE_DIR
        history.STORE_DIR = STORE
        try:
            for offset in range(0, len(symbols), CONCURRENCY):
                batch = symbols[offset:offset + CONCURRENCY]
                results = await asyncio.gather(
                    *(fetch_symbol(client, s, start, minutes) for s in batch))
                for symbol, result in zip(batch, results):
                    if isinstance(result, str):
                        failed += 1
                        if len(problems) < 5:
                            problems.append(f"{symbol}: {result}")
                        continue
                    if len(result) < 2:
                        failed += 1
                        continue
                    history.save(symbol, minutes, SESSION_ALIGNED, result)
                    stored += 1
                    depths.append(len(result))
                done = offset + len(batch)
                if done % 80 == 0 or done >= len(symbols):
                    rate = done / max(0.001, time.perf_counter() - began)
                    print(f"{DIM}  {done}/{len(symbols)}  "
                          f"{rate:.1f} symbols/s{RESET}")
                await asyncio.sleep(0.25)
        finally:
            history.STORE_DIR = original

    elapsed = time.perf_counter() - began
    print(f"\n{BOLD}{stored}{RESET} symbol(s) stored, {failed} failed, "
          f"{elapsed:.0f}s")
    if depths:
        depths.sort()
        median = depths[len(depths) // 2]
        print(f"{DIM}bars per symbol: min {depths[0]}  median {median}  "
              f"max {depths[-1]}   ~{median / (390 / minutes) / 252:.1f} years{RESET}")
        print(f"{DIM}total bars: {sum(depths):,}{RESET}")
    for problem in problems:
        print(f"  {YELLOW}{problem}{RESET}")
    print(f"\n{DIM}Next: verify it before trusting a number from it —{RESET}")
    print(f"{DIM}  python scripts/verify_bars.py   (point it at the new "
          f"store first){RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--start", default="2024-09-01",
                   help=f"earliest available is {EARLIEST}")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--minutes", type=int, default=V.interval_minutes)
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.index, a.start, a.limit, a.minutes)))
