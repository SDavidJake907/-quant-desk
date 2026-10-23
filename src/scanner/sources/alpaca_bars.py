"""Alpaca stock bars for many symbols at once (the breakout book, execution/orb.py).

One request carries up to CHUNK symbols and is paged with next_page_token.
FEEDS: this account's plan gives SIP (all exchanges) only for data older than
15 minutes ("subscription does not permit querying recent SIP data", checked
2026-10-23) and IEX (one exchange, ~2-3% of volume) in real time. The book
compares IEX with IEX -- today's opening volume against IEX's usual -- so the
ratio holds even though the absolute numbers are small.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import httpx

from scanner.config import settings

URL = "https://data.alpaca.markets/v2/stocks/bars"
CHUNK = 200
Bar = dict   # {"t", "o", "h", "l", "c", "v"} as Alpaca sends it


def _headers() -> dict[str, str]:
    return {"APCA-API-KEY-ID": settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": settings.alpaca_secret_key}


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone().isoformat(timespec="seconds")


async def bars(symbols: Sequence[str], client: httpx.AsyncClient, *, timeframe: str,
               start: datetime, end: datetime, feed: str = "iex") -> dict[str, list[Bar]]:
    """{symbol: bars oldest first} between `start` and `end`. Raises on an HTTP error:
    a half-read market is not a smaller market."""
    out: dict[str, list[Bar]] = {}
    for i in range(0, len(symbols), CHUNK):
        token = None
        while True:
            params = {"symbols": ",".join(symbols[i:i + CHUNK]), "timeframe": timeframe,
                      "start": _rfc3339(start), "end": _rfc3339(end), "feed": feed,
                      "limit": 10000, "adjustment": "raw"}
            if token:
                params["page_token"] = token
            r = await client.get(URL, headers=_headers(), params=params, timeout=30.0)
            r.raise_for_status()
            body = r.json()
            for s, rows in (body.get("bars") or {}).items():
                out.setdefault(s, []).extend(rows)
            token = body.get("next_page_token")
            if not token:
                break
    return out
