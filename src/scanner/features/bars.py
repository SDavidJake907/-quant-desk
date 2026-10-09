"""Daily price bars, fetched once and cached on disk.

Yahoo's undocumented endpoints throttle at a few hundred requests per IP per
day. A 500-name universe would burn that budget in one run, so:
  - every symbol is cached to disk keyed by trading date
  - concurrency is capped by a semaphore
  - a re-run on the same day costs zero requests

We store ADJUSTED prices — open, high, low and close alike. Unadjusted prices
make a 4:1 split look like a 75% crash, which would poison every moving average
and momentum figure downstream. Adjusting only the close is worse still: it
leaves two scales inside one bar. See `_adjustment_ratio`.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

import httpx

from scanner.config import settings
from scanner.models import Frozen

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
CACHE_DIR = Path("data/bars")

_LIMITER = asyncio.Semaphore(5)


class BarsError(RuntimeError):
    """Raised when price history cannot be obtained."""


class Row(NamedTuple):
    """One parsed bar, already on the adjusted scale."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def _adjustment_ratio(raw_close: float | None, adjusted_close: float) -> float | None:
    """Factor that carries a raw quoted price onto the adjusted scale.

    Yahoo adjusts only the close. Open, high and low arrive raw, so a series
    with any dividend or split holds two scales at once — on MO, that puts the
    close outside its own high/low on 95% of five years of daily bars.

    The consequence is not cosmetic. `backtest.setups.resolve` decides a trade
    by testing a stop and a target against `lows` and `highs`. Levels derived
    from an adjusted close, checked against raw highs, sit below almost every
    bar: the backtest fills the target immediately and reports an edge that is
    nothing but a unit mismatch.

    One ratio per bar, applied to all four prices, keeps each bar's proportions
    intact — so ATR and every other high-minus-low measure stay meaningful.
    Returns None when the raw close is missing or zero. The scale of that bar
    cannot be established, and there is no safe guess: keeping the raw high and
    low beside an adjusted close rebuilds the very inconsistency this removes.
    Such a bar is dropped, which is a data defect losing one bar rather than
    silently poisoning the series.
    """
    if not raw_close:
        return None
    return float(adjusted_close) / float(raw_close)


def _scaled(price: float | None, fallback: float | None, ratio: float) -> float:
    """A raw price carried onto the adjusted scale, or the bar's close if the
    field is missing. The fallback is scaled too — an unscaled one would
    reintroduce exactly the inconsistency this function exists to remove."""
    value = price if price is not None else fallback
    return float(value or 0.0) * ratio


class Bars(Frozen):
    """One symbol's daily history, oldest first.

    ALL FOUR PRICES ARE ON THE ADJUSTED SCALE. Yahoo returns two scales in one
    response — `adjclose` corrected for splits and dividends, `quote` raw — and
    this object used to take its closes from the first and its highs and lows
    from the second. See `_adjust` for why that is not a rounding detail.
    """

    symbol: str
    dates: tuple[date, ...]
    opens: tuple[float, ...]
    closes: tuple[float, ...]
    volumes: tuple[float, ...]
    highs: tuple[float, ...]
    lows: tuple[float, ...]

    def __len__(self) -> int:
        return len(self.closes)

    def upto(self, index: int) -> "Bars":
        """History as it looked at `index`, inclusive.

        This is what makes point-in-time backtesting possible: features
        computed from `bars.upto(i)` cannot see bar i+1. Every lookahead bug
        in a backtest is ultimately a failure to do exactly this.
        """
        stop = index + 1
        return Bars(
            symbol=self.symbol,
            dates=self.dates[:stop],
            opens=self.opens[:stop],
            closes=self.closes[:stop],
            volumes=self.volumes[:stop],
            highs=self.highs[:stop],
            lows=self.lows[:stop],
        )

    def index_of(self, on: date) -> int | None:
        """Index of the last bar on or before `on`, or None if too early."""
        found = None
        for i, bar_date in enumerate(self.dates):
            if bar_date <= on:
                found = i
            else:
                break
        return found


def parse_chart(payload: dict, symbol: str) -> Bars:
    """Pure parse. Drops any bar with a missing close so arrays stay aligned."""
    results = payload.get("chart", {}).get("result") or []
    if not results:
        raise BarsError(f"{symbol}: empty chart result")

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]

    adj = result.get("indicators", {}).get("adjclose") or []
    closes = adj[0].get("adjclose") if adj else quote.get("close")
    if not timestamps or not closes:
        raise BarsError(f"{symbol}: no usable price series")

    # The raw close is what open/high/low are quoted against, so it is the
    # denominator of the adjustment ratio — never the adjusted close.
    raw_closes = quote.get("close") or closes
    volumes = quote.get("volume") or [0] * len(timestamps)
    opens = quote.get("open") or raw_closes
    highs = quote.get("high") or raw_closes
    lows = quote.get("low") or raw_closes

    rows: list[Row] = []
    for ts, adj, raw, volume, open_, high, low in zip(
        timestamps, closes, raw_closes, volumes, opens, highs, lows
    ):
        if adj is None:
            continue
        ratio = _adjustment_ratio(raw, adj)
        if ratio is None:
            continue
        rows.append(Row(
            date=datetime.fromtimestamp(ts, timezone.utc).date(),
            open=_scaled(open_, raw, ratio),
            high=_scaled(high, raw, ratio),
            low=_scaled(low, raw, ratio),
            close=float(adj),
            volume=float(volume or 0),
        ))
    if len(rows) < 2:
        raise BarsError(f"{symbol}: fewer than 2 valid bars")

    return Bars(
        symbol=symbol,
        dates=tuple(r.date for r in rows),
        opens=tuple(r.open for r in rows),
        highs=tuple(r.high for r in rows),
        lows=tuple(r.low for r in rows),
        closes=tuple(r.close for r in rows),
        volumes=tuple(r.volume for r in rows),
    )


_DAYS_PER_YEAR = 365.25
_DAYS_PER_MONTH = 30.5


def range_start(range_: str, today: date | None = None) -> date:
    """First calendar date a Yahoo range string covers ("5y", "6mo", "max").

    Deliberately approximate: it decides which symbols a backtest fetches, and
    an extra week of slack costs nothing, while coming up short silently
    shrinks the universe. Unparseable input falls back to the fetch default.
    """
    today = today or date.today()
    text = range_.strip().lower()
    if text == "max":
        return date(1970, 1, 1)
    if text == "ytd":
        return date(today.year, 1, 1)
    unit_days = _DAYS_PER_MONTH if text.endswith("mo") else _DAYS_PER_YEAR
    try:
        value = float(text.rstrip("ymod"))
    except ValueError:
        value, unit_days = 2.0, _DAYS_PER_YEAR
    return today - timedelta(days=int(value * unit_days))


def to_yahoo(symbol: str) -> str:
    """Index-convention ticker -> Yahoo's spelling.

    Share classes are written BRK.B by S&P and Wikipedia, BRK-B by Yahoo.
    Sending the dotted form gets a 404 that lands in `fetch_many`'s error
    list, so two S&P 500 members were quietly absent from every run.
    """
    return symbol.replace(".", "-")


def _cache_path(symbol: str, range_: str, interval: str) -> Path:
    """Range AND interval are part of the key.

    Without the range, a 2y fetch followed by a 5y fetch silently returns the
    2y series — a backtest quietly running on a fraction of the data it thinks
    it has, with no error anywhere.

    Interval is the same trap one level down: ask for 60-minute bars after a
    daily fetch and you would get daily bars back, and a 4-hour screen would
    read them as intraday structure without complaining once.
    """
    safe = symbol.replace("/", "_").replace("^", "IDX_")
    return CACHE_DIR / f"{safe}__{range_}__{interval}.json"


def _read_cache(
    symbol: str, range_: str, interval: str, ttl_seconds: float | None = None
) -> dict | None:
    """Today's cached payload, or None.

    `ttl_seconds` is required for intraday data and must not be omitted.
    A date-stamped cache is correct for daily bars — they change once a day —
    and silently catastrophic for intraday ones: a payload fetched at 03:49
    still reads as "today" at 15:00, so a live scan would size orders from
    yesterday's close while believing it had current prices. Measured on
    2026-08-06: the cache served 16:00 bars from the previous session ninety
    minutes into an open market.
    """
    path = _cache_path(symbol, range_, interval)
    if not path.exists():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    if ttl_seconds is None:
        if cached.get("cached_on") != date.today().isoformat():
            return None
    else:
        stamped = cached.get("cached_at")
        if not isinstance(stamped, (int, float)):
            return None  # written before ages were recorded — refetch
        if time.time() - stamped > ttl_seconds:
            return None
    return cached.get("payload")


async def fetch_payload(
    symbol: str, client: httpx.AsyncClient, *, range_: str = "2y",
    interval: str = "1d", ttl_seconds: float | None = None,
) -> dict:
    """Fetch (or reuse today's cache of) one symbol's raw chart payload.

    Kept separate from `fetch_bars` so intraday callers can parse the same
    response into their own shape without a second request or a second cache.
    """
    cached = _read_cache(symbol, range_, interval, ttl_seconds)
    if cached is not None:
        return cached

    async with _LIMITER:
        try:
            response = await client.get(
                CHART_URL.format(symbol=to_yahoo(symbol)),
                params={"range": range_, "interval": interval, "events": "div,split"},
                headers={"User-Agent": settings.user_agent},
                timeout=settings.http_timeout_s,
                follow_redirects=True,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BarsError(f"{symbol}: fetch failed: {exc}") from exc


def cached_payload(
    symbol: str, range_: str, interval: str, ttl_seconds: float | None = None
) -> dict | None:
    """The cached payload if it is still fresh, else None.

    Public because a caller has to be able to tell a cache HIT from a fetch.
    `fetch_payload` returns the payload either way, and `cache_payload` then
    rewrote it unconditionally — which reset `cached_at` on every read, so a
    stale payload renewed its own lease and could outlive its TTL forever.
    """
    return _read_cache(symbol, range_, interval, ttl_seconds)


def cache_payload(symbol: str, range_: str, interval: str, payload: dict) -> None:
    """Write a payload the caller has already parsed successfully.

    Callers parse first and cache second, deliberately. Yahoo answers 200 with
    an empty series for a symbol it does not know, and caching that pins the
    failure in place for the rest of the day — including across the fix that
    would have cured it.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(symbol, range_, interval).write_text(
        json.dumps({
            "cached_on": date.today().isoformat(),
            "cached_at": time.time(),
            "payload": payload,
        }),
        encoding="utf-8",
    )


async def fetch_bars(
    symbol: str, client: httpx.AsyncClient, *, range_: str = "2y",
    ttl_seconds: float | None = None,
) -> Bars:
    """Fetch (or reuse today's cache of) daily bars for one symbol.

    `ttl_seconds` caps the cache's age: a caller that needs TODAY's close after
    the market shuts (the competition's 13:30 scoring) must not reuse bars cached
    that morning. Left out, the day-stamped cache applies as before."""
    payload = await fetch_payload(symbol, client, range_=range_, interval="1d",
                                  ttl_seconds=ttl_seconds)
    bars = parse_chart(payload, symbol)  # parse before caching; never cache garbage
    cache_payload(symbol, range_, "1d", payload)
    return bars


async def fetch_many(
    symbols: tuple[str, ...], client: httpx.AsyncClient, *, range_: str = "2y",
    ttl_seconds: float | None = None,
) -> tuple[dict[str, Bars], tuple[str, ...]]:
    """Fetch many symbols concurrently. Returns (bars_by_symbol, errors)."""
    settled = await asyncio.gather(
        *(fetch_bars(s, client, range_=range_, ttl_seconds=ttl_seconds) for s in symbols),
        return_exceptions=True,
    )
    bars: dict[str, Bars] = {}
    errors: list[str] = []
    for symbol, outcome in zip(symbols, settled):
        if isinstance(outcome, Bars):
            bars[symbol] = outcome
        else:
            errors.append(f"{symbol}: {outcome}")
    return bars, tuple(errors)
