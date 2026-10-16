"""Live market snapshot for Gemma's morning brief.

Reads current futures, bond yields, VIX, and key levels from Yahoo Finance
so Gemma sees today's market backdrop when she decides what to buy.

THESE ARE SNAPSHOTS, NOT SIGNALS
Every number here is context — what the market is doing right now — not a
prediction of what it will do. The prompt tells Gemma so explicitly.

PURE BY DEFAULT
fetch_snapshot() makes Yahoo requests and never raises. A missing feed returns
None for that line, never an exception.
"""

from __future__ import annotations

import httpx
from dataclasses import dataclass
from typing import Any


#: Yahoo tickers for the snapshot. Futures are the front contract.
YAHOO_TICKERS: dict[str, str] = {
    "^GSPC": "S&P 500",
    "^DJI": "Dow Jones",
    "^IXIC": "Nasdaq Composite",
    "^RUT": "Russell 2000",
    "^VIX": "VIX (fear)",
    "^TNX": "10-Year Yield",
    "^TYX": "30-Year Yield",
    "^IRX": "13-Week T-Bill",
    "^VIX3M": "3-Month VIX",
    "ES=F": "S&P 500 Futures",
    "NQ=F": "Nasdaq 100 Futures",
    "YM=F": "Dow Futures",
    "RTY=F": "Russell 2000 Futures",
    "ZB=F": "30-Year Bond Futures",
    "ZN=F": "10-Year Note Futures",
    "GC=F": "Gold Futures",
    "CL=F": "Crude Oil Futures",
    "DX=F": "US Dollar Index",
    "BTC-USD": "Bitcoin",
    "EURUSD=X": "EUR/USD",
}


@dataclass(frozen=True)
class MarketQuote:
    """One live quote from Yahoo."""

    symbol: str
    name: str
    price: float
    change: float
    change_pct: float
    previous_close: float | None = None


@dataclass(frozen=True)
class MarketSnapshot:
    """All quotes fetched at once."""

    quotes: tuple[MarketQuote, ...]
    fetched_at: str

    def line(self, symbol: str) -> str | None:
        """Formatted line for one symbol, or None if not found."""
        for q in self.quotes:
            if q.symbol == symbol:
                sign = "+" if q.change >= 0 else ""
                return f"{q.name} {q.price:,.2f} ({sign}{q.change:,.2f}, {sign}{q.change_pct:+.2f}%)"
        return None

    def lines(self, symbols: tuple[str, ...]) -> tuple[str, ...]:
        """Formatted lines for multiple symbols, in order."""
        return tuple(
            line for line in (self.line(s) for s in symbols) if line is not None
        )


async def fetch_snapshot(client: httpx.AsyncClient) -> MarketSnapshot:
    """Fetch current quotes for all Yahoo tickers. Never raises.

    Yahoo's chart endpoint returns the last trade and previous close. On any
    failure the quote is skipped — a missing feed degrades gracefully.
    """
    quotes: list[MarketQuote] = []
    for symbol, name in YAHOO_TICKERS.items():
        try:
            quote = await _fetch_one(client, symbol, name)
            if quote is not None:
                quotes.append(quote)
        except Exception:
            continue  # one bad ticker never kills the snapshot

    from datetime import datetime, timezone

    return MarketSnapshot(
        quotes=tuple(quotes),
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


async def _fetch_one(
    client: httpx.AsyncClient, symbol: str, name: str
) -> MarketQuote | None:
    """Fetch one Yahoo chart quote."""
    try:
        response = await client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={
                "range": "1d",
                "interval": "1m",
                "includePrePost": "true",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15.0,
        )
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]

        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        if not timestamps or not closes:
            return None

        # Last non-null close
        last_close = None
        for c in reversed(closes):
            if c is not None:
                last_close = float(c)
                break
        if last_close is None:
            return None

        # Previous close from metadata or last complete bar
        meta = result.get("meta", {})
        previous_close = meta.get("previousClose")
        previous_close = float(previous_close) if previous_close else None

        change = last_close - previous_close if previous_close else 0.0
        change_pct = (change / previous_close * 100.0) if previous_close else 0.0

        return MarketQuote(
            symbol=symbol,
            name=name,
            price=last_close,
            change=change,
            change_pct=change_pct,
            previous_close=previous_close,
        )
    except Exception:
        return None


def summary_text(snapshot: MarketSnapshot) -> str:
    """One-line market snapshot for Gemma's facts block."""

    def line_for(symbol: str) -> str | None:
        return snapshot.line(symbol)

    spx = line_for("^GSPC")
    vix = line_for("^VIX")
    tnx = line_for("^TNX")
    es = line_for("ES=F")
    nq = line_for("NQ=F")
    gc = line_for("GC=F")
    cl = line_for("CL=F")

    parts: list[str] = []
    if es:
        parts.append(f"ES futures {es.split(' ', 1)[1]}")
    if nq:
        parts.append(f"NQ futures {nq.split(' ', 1)[1]}")
    if spx:
        parts.append(f"S&P {spx.split(' ', 1)[1]}")
    if vix:
        parts.append(f"VIX {vix.split(' ', 1)[1]}")
    if tnx:
        parts.append(f"10Y {tnx.split(' ', 1)[1]}")
    if gc:
        parts.append(f"Gold {gc.split(' ', 1)[1]}")
    if cl:
        parts.append(f"Oil {cl.split(' ', 1)[1]}")

    return " | ".join(parts)
