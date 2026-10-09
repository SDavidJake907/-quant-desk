"""Orders and prices outside regular hours (WORKLIST §59).

Alpaca allows only LIMIT orders, time_in_force "day", extended_hours true, and no
stop or bracket legs. So an extended-hours buy goes in as a plain limit order;
its stop and target are still logged with the intent, and protect.py arms them
as an OCO at the first regular-hours run (05:33 AK), exactly as it re-arms any
position that lost its stop.

PRICES on this account's free data plan (checked 2026-10-09): recent SIP and
BOATS data are refused; IEX shows only the regular-session close after hours.
What works: Yahoo's 1-minute chart with pre/post market (premarket, after-hours)
and Alpaca's "overnight" feed (20:00-04:00 ET). A price older than the freshness
limit is refused -- an order is never priced off a stale number.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from scanner.config import settings
from scanner.execution.alpaca import round_tick
from scanner.execution.sessions import OVERNIGHT

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
ALPACA_LATEST = "https://data.alpaca.markets/v2/stocks/trades/latest"
#: Newest acceptable price, by session. Overnight trading is thin.
FRESH = {"pre": timedelta(minutes=15), "after": timedelta(minutes=15),
         "overnight": timedelta(minutes=45)}


def limit_payload(symbol: str, qty: int, side: str, limit: float,
                  client_order_id: str) -> dict[str, Any]:
    if qty < 1:
        raise ValueError("size came out at zero shares")
    return {"symbol": symbol, "qty": str(qty), "side": side, "type": "limit",
            "limit_price": f"{round_tick(limit):.2f}", "time_in_force": "day",
            "extended_hours": True, "client_order_id": client_order_id}


def fresh_enough(stamp: datetime, now: datetime, session: str) -> bool:
    return now - stamp <= FRESH.get(session, timedelta(minutes=15))


async def price(symbol: str, client: httpx.AsyncClient, session: str,
                now: datetime | None = None) -> float | None:
    """The latest trade in this session, or None if nothing fresh."""
    now = now or datetime.now(timezone.utc)
    try:
        if session == OVERNIGHT:
            r = await client.get(ALPACA_LATEST, params={"symbols": symbol, "feed": "overnight"},
                                 headers={"APCA-API-KEY-ID": settings.alpaca_key_id,
                                          "APCA-API-SECRET-KEY": settings.alpaca_secret_key},
                                 timeout=20)
            r.raise_for_status()
            trade = (r.json().get("trades") or {}).get(symbol)
            if not trade:
                return None
            stamp = datetime.fromisoformat(trade["t"][:26].rstrip("Z") + "+00:00")
            return float(trade["p"]) if fresh_enough(stamp, now, session) else None
        r = await client.get(YAHOO_CHART.format(symbol=symbol),
                             params={"range": "1d", "interval": "1m", "includePrePost": "true"},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        bars = [(t, c) for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"])
                if c is not None]
        if not bars:
            return None
        stamp = datetime.fromtimestamp(bars[-1][0], timezone.utc)
        return float(bars[-1][1]) if fresh_enough(stamp, now, session) else None
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return None
