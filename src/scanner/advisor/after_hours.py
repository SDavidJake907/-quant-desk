"""What Gemma sees outside regular hours (24/5, operator 2026-10-22).

Her facts are built from daily bars, so after the 16:00 ET close they stop at
the close: an earnings pop like RNG's +13% at 16:20 ET was invisible to her.
Outside regular hours she now also gets
  - each stock's latest pre/after-hours trade vs the regular close
  - S&P 500 and Nasdaq 100 futures vs their last settlement (they trade
    Sunday 18:00 -> Friday 17:00 ET, paused 17:00-18:00 daily)
from Yahoo's chart with pre/post market. Facts only, labelled with their time.
Overnight (20:00-04:00 ET) Yahoo has no stock trades: the line says so.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

ET = ZoneInfo("America/New_York")
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
FUTURES = {"S&P 500": "ES=F", "Nasdaq 100": "NQ=F"}
#: A stock trade older than this is reported as "no trade in the last hour".
FRESH = timedelta(hours=1)


@dataclass(frozen=True)
class Quote:
    last: float          # latest trade, any session
    ref: float           # stocks: the regular close; futures: the last settlement
    at: datetime         # time of `last`, UTC


async def quote(symbol: str, client: httpx.AsyncClient, *, futures: bool = False) -> Quote | None:
    """Latest trade vs its reference, or None when Yahoo has nothing."""
    try:
        r = await client.get(CHART.format(symbol), headers={"User-Agent": "Mozilla/5.0"},
                             params={"range": "1d", "interval": "1m", "includePrePost": "true"},
                             timeout=20)
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        meta = res["meta"]
        bars = [(t, c) for t, c in zip(res.get("timestamp") or [],
                                       res["indicators"]["quote"][0]["close"]) if c is not None]
        if not bars:
            return None
        ref = float(meta["previousClose"] if futures else meta["regularMarketPrice"])
        return Quote(last=float(bars[-1][1]), ref=ref,
                     at=datetime.fromtimestamp(bars[-1][0], timezone.utc))
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return None


def _move(q: Quote) -> float:
    return (q.last / q.ref - 1) * 100 if q.ref else 0.0


def stock_line(q: Quote | None, now: datetime) -> str:
    if q is None:
        return "after-hours: no trade since the close"
    if now - q.at > FRESH:
        return (f"after-hours: no trade in the last hour (last {q.last:,.2f} at "
                f"{q.at.astimezone(ET):%H:%M} ET, {_move(q):+.1f}% vs the {q.ref:,.2f} close)")
    return (f"after-hours: {q.last:,.2f} at {q.at.astimezone(ET):%H:%M} ET, "
            f"{_move(q):+.1f}% vs the {q.ref:,.2f} close")


def futures_text(quotes: Mapping[str, Quote | None], now: datetime) -> str:
    parts = [f"{name} futures {q.last:,.2f} ({_move(q):+.2f}% vs the last settlement, "
             f"{q.at.astimezone(ET):%H:%M} ET)" for name, q in quotes.items() if q is not None]
    return "; ".join(parts)


async def futures(client: httpx.AsyncClient) -> dict[str, Quote | None]:
    return {name: await quote(sym, client, futures=True) for name, sym in FUTURES.items()}
