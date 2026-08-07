"""Global index quotes from Yahoo's chart endpoint.

Endpoint: query1.finance.yahoo.com/v8/finance/chart/<symbol> (free, no key).

CORRECTNESS WARNING, learned by inspection:
`meta.chartPreviousClose` is NOT yesterday's close — it is the close at the
start of whatever `range` you asked for. At range=5d it reported a 5-day-old
value. Using it would silently render a five-day move as a daily move.

So we ignore that field entirely and derive the previous close from the daily
bars: the last session that closed strictly before the current session date,
evaluated in the exchange's own timezone.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from scanner.config import settings
from scanner.models import Frozen

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


class QuoteError(RuntimeError):
    """Raised when a quote cannot be fetched or is internally inconsistent."""


class IndexQuote(Frozen):
    """One index, point-in-time. Every number here comes from the feed."""

    symbol: str
    name: str
    region: str
    country: str
    currency: str
    price: float
    previous_close: float
    day_high: float | None = None
    day_low: float | None = None
    week52_high: float | None = None
    week52_low: float | None = None
    exchange_tz: str
    quote_time: datetime
    is_open: bool

    @property
    def change(self) -> float:
        return self.price - self.previous_close

    @property
    def change_pct(self) -> float:
        if self.previous_close == 0:
            return 0.0
        return (self.change / self.previous_close) * 100.0

    @property
    def local_time(self) -> datetime:
        return self.quote_time.astimezone(ZoneInfo(self.exchange_tz))

    @property
    def pct_off_52w_high(self) -> float | None:
        if not self.week52_high:
            return None
        return ((self.price - self.week52_high) / self.week52_high) * 100.0


def _previous_close(
    timestamps: list[int], closes: list[float | None], session_date, tz: ZoneInfo
) -> float | None:
    """Last close from a session strictly before the current one."""
    for ts, close in sorted(zip(timestamps, closes), reverse=True):
        if close is None:
            continue
        bar_date = datetime.fromtimestamp(ts, tz).date()
        if bar_date < session_date:
            return float(close)
    return None


def parse_chart(payload: dict, symbol: str, name: str, region: str, country: str) -> IndexQuote:
    """Pure parse step — no I/O, so the tricky date logic is unit-testable."""
    results = payload.get("chart", {}).get("result") or []
    if not results:
        raise QuoteError(f"{symbol}: empty chart result")

    result = results[0]
    meta = result.get("meta", {})

    price = meta.get("regularMarketPrice")
    tz_name = meta.get("exchangeTimezoneName")
    market_time = meta.get("regularMarketTime")
    if price is None or not tz_name or not market_time:
        raise QuoteError(f"{symbol}: missing price/timezone/time in meta")

    tz = ZoneInfo(tz_name)
    quote_time = datetime.fromtimestamp(market_time, timezone.utc)
    session_date = quote_time.astimezone(tz).date()

    timestamps = result.get("timestamp") or []
    closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []

    previous = _previous_close(timestamps, closes, session_date, tz)
    if previous is None:
        # Refuse to guess. A wrong baseline produces a wrong percentage, which
        # is worse than no number at all.
        raise QuoteError(f"{symbol}: could not establish a previous close")

    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    now = int(datetime.now(timezone.utc).timestamp())
    start, end = period.get("start"), period.get("end")
    is_open = bool(start and end and start <= now <= end)

    return IndexQuote(
        symbol=symbol,
        name=name or meta.get("shortName", symbol),
        region=region,
        country=country,
        currency=meta.get("currency", ""),
        price=float(price),
        previous_close=previous,
        day_high=meta.get("regularMarketDayHigh"),
        day_low=meta.get("regularMarketDayLow"),
        week52_high=meta.get("fiftyTwoWeekHigh"),
        week52_low=meta.get("fiftyTwoWeekLow"),
        exchange_tz=tz_name,
        quote_time=quote_time,
        is_open=is_open,
    )


async def fetch_index(
    symbol: str, name: str, region: str, country: str, client: httpx.AsyncClient
) -> IndexQuote:
    """Fetch one index. 5d range gives enough bars to survive holidays."""
    try:
        response = await client.get(
            CHART_URL.format(symbol=symbol),
            params={"range": "5d", "interval": "1d"},
            headers={"User-Agent": settings.user_agent},
            timeout=settings.http_timeout_s,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise QuoteError(f"{symbol}: fetch failed: {exc}") from exc

    return parse_chart(payload, symbol, name, region, country)


async def fetch_all(
    definitions: tuple[tuple[str, str, str, str], ...], client: httpx.AsyncClient
) -> tuple[tuple[IndexQuote, ...], tuple[str, ...]]:
    """Fetch every index concurrently.

    Returns (quotes, errors). A dead symbol must never take down the board.
    """
    tasks = [fetch_index(sym, name, region, country, client) for sym, name, region, country in definitions]
    settled = await asyncio.gather(*tasks, return_exceptions=True)

    quotes: list[IndexQuote] = []
    errors: list[str] = []
    for outcome in settled:
        if isinstance(outcome, IndexQuote):
            quotes.append(outcome)
        else:
            errors.append(str(outcome))
    return tuple(quotes), tuple(errors)
