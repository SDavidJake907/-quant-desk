"""Market capitalisation for every US-listed stock, from Nasdaq's public screener.

One request returns ~7,000 rows (symbol, last sale, market cap). Free, no key;
Nasdaq rejects generic clients, so it gets the same browser-shaped headers as
sources/calendar.py. Cached per day in data/market_caps/YYYY-MM-DD.json -- caps
move with price, but a $2B floor does not need intraday precision.

A failed fetch returns {} and callers treat a missing cap as "unknown, keep":
a data outage must never empty a pool.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import httpx

URL = "https://api.nasdaq.com/api/screener/stocks"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
CACHE = Path("data/market_caps")


def normalize(symbol: str) -> str:
    """Nasdaq writes share classes as BRK/B or BRK^B; the rest of the repo uses BRK-B."""
    return symbol.strip().upper().replace("/", "-").replace("^", "-")


def parse(rows: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """{symbol: market cap in dollars}; rows without a positive cap are skipped."""
    out: dict[str, float] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        try:
            cap = float(str(row.get("marketCap") or "").replace(",", ""))
        except ValueError:
            continue
        if symbol and cap > 0:
            out[normalize(symbol)] = cap
    return out


async def load(client: httpx.AsyncClient, *, today: date | None = None,
               cache_dir: Path = CACHE) -> dict[str, float]:
    """Today's caps, from the cache or one request; {} if Nasdaq is unavailable."""
    path = cache_dir / f"{(today or date.today()).isoformat()}.json"
    if path.exists():
        try:
            return {k: float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError):
            pass
    try:
        response = await client.get(URL, params={"tableonly": "true", "limit": "10000",
                                                 "download": "true"},
                                    headers=HEADERS, timeout=60.0)
        response.raise_for_status()
        caps = parse(response.json()["data"]["rows"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        print(f"  market caps unavailable ({type(exc).__name__}: {exc}) -- no cap floor today")
        return {}
    if caps:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(caps), encoding="utf-8")
        tmp.replace(path)
    return caps
