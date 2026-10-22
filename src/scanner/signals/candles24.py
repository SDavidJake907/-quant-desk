"""Round-the-clock candles for the 24h signal books (operator 2026-10-21: "remember 24/5").

  premarket + regular + after-hours (04:00-20:00 ET): Yahoo 5m with includePrePost
  overnight (20:00-04:00 ET, Sunday night to Friday morning): Alpaca BOATS 5m
    (Blue Ocean ATS -- the venue Alpaca routes overnight orders to)
Merged by time into one 5-minute series, then grouped by the clock into 15m.

Overnight candles are THIN (a few hundred shares a candle on a name that
trades a million in regular hours), so the 24h books are kept apart from the
regular-hours books and never mixed into their scores.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone

import httpx

from scanner.config import settings

Candle = tuple[datetime, float, float, float, float, float]   # UTC start, o, h, l, c, v
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
ALPACA = "https://data.alpaca.markets/v2/stocks/bars"


async def yahoo_5m(symbol: str, client: httpx.AsyncClient, range_: str = "5d") -> list[Candle]:
    r = await client.get(YAHOO.format(symbol), headers={"User-Agent": "Mozilla/5.0"},
                         params={"interval": "5m", "range": range_, "includePrePost": "true"})
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = []
    for i, ts in enumerate(res.get("timestamp") or []):
        vals = [q[k][i] for k in ("open", "high", "low", "close", "volume")]
        if None not in vals[:4]:
            out.append((datetime.fromtimestamp(ts, timezone.utc), *map(float, vals[:4]), float(vals[4] or 0)))
    return out


async def boats_5m(symbols: Sequence[str], client: httpx.AsyncClient, start: datetime) -> dict[str, list[Candle]]:
    head = {"APCA-API-KEY-ID": settings.alpaca_key_id, "APCA-API-SECRET-KEY": settings.alpaca_secret_key}
    out: dict[str, list[Candle]] = {s: [] for s in symbols}
    token = None
    while True:
        params = {"symbols": ",".join(symbols), "timeframe": "5Min", "feed": "boats", "limit": 10000,
                  "start": start.strftime("%Y-%m-%dT%H:%M:%SZ")}
        if token:
            params["page_token"] = token
        r = await client.get(ALPACA, headers=head, params=params)
        r.raise_for_status()
        body = r.json()
        for s, rows in (body.get("bars") or {}).items():
            out.setdefault(s, []).extend(
                (datetime.fromisoformat(b["t"].replace("Z", "+00:00")), float(b["o"]), float(b["h"]),
                 float(b["l"]), float(b["c"]), float(b["v"])) for b in rows)
        token = body.get("next_page_token")
        if not token:
            return out


def merge(*series: Iterable[Candle]) -> list[Candle]:
    """One series by start time; the first source wins a duplicate time."""
    seen: dict[datetime, Candle] = {}
    for s in series:
        for c in s:
            seen.setdefault(c[0], c)
    return [seen[t] for t in sorted(seen)]


def group(candles: Sequence[Candle], minutes: int) -> list[Candle]:
    """Clock-aligned buckets (15m: :00/:15/:30/:45)."""
    out: list[Candle] = []
    for t, o, h, lo, c, v in candles:
        start = t - timedelta(minutes=t.minute % minutes, seconds=t.second, microseconds=t.microsecond)
        if out and out[-1][0] == start:
            p = out[-1]
            out[-1] = (start, p[1], max(p[2], h), min(p[3], lo), c, p[5] + v)
        else:
            out.append((start, o, h, lo, c, v))
    return out


def completed(candles: Sequence[Candle], minutes: int, now: datetime) -> list[Candle]:
    """Drop a candle whose bucket has not closed yet."""
    if candles and now < candles[-1][0] + timedelta(minutes=minutes):
        return list(candles[:-1])
    return list(candles)
