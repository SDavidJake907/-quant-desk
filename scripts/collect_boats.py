"""Collect OVERNIGHT candles (Alpaca BOATS -- Blue Ocean ATS, 20:00-04:00 ET) for the S&P 500.

    python scripts/collect_boats.py --start 2024-09-16 --minutes 30

Operator 2026-10-22: "its open 24/5 thats why im trying to find a stragtie". The
overnight venue starts 2024-09-16 on this feed. Stored per symbol as
data/intraday_boats/SYM__30m.json: [[epoch, o, h, l, c, v], ...] -- the same row
shape as the SIP stores, so pre/regular/after (collect_alpaca --extended) and
overnight can be merged into one 24/5 series. Read-only research data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.sources import composite
from scanner.sources.index_history import SP500

STORE = Path("data/intraday_boats")
URL = "https://data.alpaca.markets/v2/stocks/bars"
BATCH = 50


async def fetch(client, symbols, start: str, minutes: int) -> dict[str, list]:
    head = {"APCA-API-KEY-ID": settings.alpaca_key_id, "APCA-API-SECRET-KEY": settings.alpaca_secret_key}
    out: dict[str, list] = {s: [] for s in symbols}
    token = None
    while True:
        params = {"symbols": ",".join(symbols), "timeframe": f"{minutes}Min", "feed": "boats",
                  "start": f"{start}T00:00:00Z", "limit": 10000, "adjustment": "all"}
        if token:
            params["page_token"] = token
        r = await client.get(URL, headers=head, params=params)
        if r.status_code == 429:
            await asyncio.sleep(3)
            continue
        r.raise_for_status()
        body = r.json()
        for s, rows in (body.get("bars") or {}).items():
            out[s] += [[datetime.fromisoformat(b["t"].replace("Z", "+00:00")).timestamp(), float(b["o"]),
                        float(b["h"]), float(b["l"]), float(b["c"]), float(b["v"])] for b in rows]
        token = body.get("next_page_token")
        if not token:
            return out


async def main(start: str, minutes: int) -> int:
    STORE.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=120) as client:
        members = sorted({m.symbol for m in await composite.load_universe(client, (SP500,))})
        total = 0
        for k in range(0, len(members), BATCH):
            chunk = members[k:k + BATCH]
            got = await fetch(client, chunk, start, minutes)
            for s, rows in got.items():
                (STORE / f"{s}__{minutes}m.json").write_text(json.dumps(rows))
                total += len(rows)
            print(f"  {k + len(chunk)}/{len(members)} names, {total:,} overnight candles", flush=True)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default="2024-09-16")
    p.add_argument("--minutes", type=int, default=30)
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.start, a.minutes)))
