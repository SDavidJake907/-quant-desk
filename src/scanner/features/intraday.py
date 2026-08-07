"""Intraday bars, resampled from Yahoo's hourly series.

WHY RESAMPLE AT ALL
Yahoo serves 1m/5m/15m/30m/60m and then jumps straight to 1d. There is no
4-hour interval, so a 4-hour bar has to be built. It is built from 60-minute
bars because that is the coarsest input that divides cleanly.

WHAT "4 HOURS" MEANS FOR A STOCK — READ THIS BEFORE COMPARING TO A CHART
A US equity session is six and a half hours, which four does not divide. So a
session-aligned 4H series is one full bar (09:30-13:30) plus a 2.5-hour stub
(13:30-16:00). Every platform resolves this differently: some run continuous
4-hour blocks from midnight, some align to the session as we do here, some
extend into pre/post market. The candles are genuinely different objects, and
so are the swing highs computed from them.

That is not a rounding difference. A structure detector reads highs and lows,
and a bar boundary in a different place produces a different high. If the
output has to agree with a particular chart, match that chart's convention
first — `SESSION_ALIGNED` and `CONTINUOUS` exist for that.

DEPTH
Yahoo caps 60-minute history at roughly 730 days, so a 4H series runs about
1,400 bars — enough to backtest, and still one market regime.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from scanner.features.bars import BarsError, cache_payload, fetch_payload
from scanner.models import Frozen

#: US equities trade on New York time; bucket boundaries are meaningless in UTC.
EXCHANGE_TZ = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)

#: Buckets measured from the opening bell. The last bar of the day is short.
SESSION_ALIGNED = "session"
#: Buckets measured from midnight, as most crypto-native platforms do.
CONTINUOUS = "continuous"

#: Yahoo's history caps, measured. Sub-hourly data is capped far shorter,
#: which is the binding constraint on any 30-minute study.
MAX_INTRADAY_RANGE = "730d"     # 60-minute source
MAX_SUBHOURLY_RANGE = "60d"     # 30m/15m/5m source — about 86 calendar days


def source_interval(minutes: int) -> str:
    """The Yahoo interval to build `minutes`-minute bars from.

    Whole hours are built from 60-minute bars, which reach back two years.
    Anything finer must be requested at its own resolution and is capped at
    60 days — so a 30-minute backtest gets a twelfth of the history a 4-hour
    one does, and no amount of code changes that.
    """
    return "60m" if minutes % 60 == 0 else f"{minutes}m"


def max_range(minutes: int) -> str:
    return MAX_INTRADAY_RANGE if minutes % 60 == 0 else MAX_SUBHOURLY_RANGE


class IntradayBars(Frozen):
    """One symbol's intraday history, oldest first.

    Separate from `Bars` on purpose: `Bars.dates` holds dates, and two bars on
    the same day would collide there. Times are bucket STARTS, in exchange
    local time.
    """

    symbol: str
    interval_minutes: int
    alignment: str
    times: tuple[datetime, ...]
    opens: tuple[float, ...]
    highs: tuple[float, ...]
    lows: tuple[float, ...]
    closes: tuple[float, ...]
    volumes: tuple[float, ...]

    def __len__(self) -> int:
        return len(self.closes)

    def upto(self, index: int) -> "IntradayBars":
        """History as it looked at `index`, inclusive.

        The same guarantee `Bars.upto` gives daily data: a feature computed
        here cannot see bar index+1.
        """
        stop = index + 1
        return IntradayBars(
            symbol=self.symbol,
            interval_minutes=self.interval_minutes,
            alignment=self.alignment,
            times=self.times[:stop],
            opens=self.opens[:stop],
            highs=self.highs[:stop],
            lows=self.lows[:stop],
            closes=self.closes[:stop],
            volumes=self.volumes[:stop],
        )

    def sessions(self) -> tuple[date, ...]:
        return tuple(t.date() for t in self.times)


#: Named parts of the session. The open is separated because it is
#: structurally the noisiest: overnight imbalance, the opening auction and the
#: first range all resolve there, and a "liquidity sweep" during it is often
#: just the opening range forming rather than stops being taken.
SESSION_SLOTS = ("open", "morning", "midday", "close")


def session_slot(moment: datetime) -> str:
    """Which part of the trading day `moment` falls in."""
    local = moment.astimezone(EXCHANGE_TZ)
    minutes = local.hour * 60 + local.minute
    if minutes < 10 * 60 + 30:
        return "open"
    if minutes < 12 * 60:
        return "morning"
    if minutes < 14 * 60:
        return "midday"
    return "close"


def bucket_start(moment: datetime, minutes: int, alignment: str) -> datetime:
    """The start of the bucket `moment` belongs to, in exchange local time."""
    local = moment.astimezone(EXCHANGE_TZ)
    if alignment == CONTINUOUS:
        since_midnight = local.hour * 60 + local.minute
        start = (since_midnight // minutes) * minutes
        return local.replace(
            hour=start // 60, minute=start % 60, second=0, microsecond=0
        )

    open_at = datetime.combine(local.date(), SESSION_OPEN, tzinfo=EXCHANGE_TZ)
    if local < open_at:
        # Pre-market prints belong to the session they precede.
        return open_at
    index = int((local - open_at).total_seconds() // (minutes * 60))
    return open_at + timedelta(minutes=minutes * index)


def resample(
    payload: dict,
    symbol: str,
    *,
    minutes: int = 240,
    alignment: str = SESSION_ALIGNED,
) -> IntradayBars:
    """Fold a chart payload into `minutes`-minute bars. Pure function.

    Open is the first hourly open in the bucket, close the last close, high
    and low the extremes — the only aggregation that keeps the bar honest.
    Hours with a missing close are skipped rather than interpolated: an
    invented price is worse than a shorter bar.
    """
    if minutes <= 0:
        raise BarsError(f"{symbol}: interval must be positive, got {minutes}")

    results = payload.get("chart", {}).get("result") or []
    if not results:
        raise BarsError(f"{symbol}: empty chart result")

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    if not timestamps or not closes:
        raise BarsError(f"{symbol}: no usable intraday series")

    opens = quote.get("open") or closes
    highs = quote.get("high") or closes
    lows = quote.get("low") or closes
    volumes = quote.get("volume") or [0] * len(timestamps)

    buckets: dict[datetime, dict] = {}
    order: list[datetime] = []
    for i, stamp in enumerate(timestamps):
        close = closes[i] if i < len(closes) else None
        if close is None:
            continue
        start = bucket_start(
            datetime.fromtimestamp(stamp, tz=EXCHANGE_TZ), minutes, alignment
        )
        high = highs[i] if i < len(highs) and highs[i] is not None else close
        low = lows[i] if i < len(lows) and lows[i] is not None else close
        open_ = opens[i] if i < len(opens) and opens[i] is not None else close

        bucket = buckets.get(start)
        if bucket is None:
            buckets[start] = {
                "o": float(open_), "h": float(high), "l": float(low),
                "c": float(close), "v": float(volumes[i] or 0),
            }
            order.append(start)
        else:
            bucket["h"] = max(bucket["h"], float(high))
            bucket["l"] = min(bucket["l"], float(low))
            bucket["c"] = float(close)  # timestamps arrive in order
            bucket["v"] += float(volumes[i] or 0)

    if len(order) < 2:
        raise BarsError(f"{symbol}: fewer than 2 intraday bars")

    order.sort()
    return IntradayBars(
        symbol=symbol,
        interval_minutes=minutes,
        alignment=alignment,
        times=tuple(order),
        opens=tuple(buckets[t]["o"] for t in order),
        highs=tuple(buckets[t]["h"] for t in order),
        lows=tuple(buckets[t]["l"] for t in order),
        closes=tuple(buckets[t]["c"] for t in order),
        volumes=tuple(buckets[t]["v"] for t in order),
    )


async def fetch_intraday(
    symbol: str,
    client: httpx.AsyncClient,
    *,
    minutes: int = 240,
    range_: str | None = None,
    alignment: str = SESSION_ALIGNED,
    ttl_seconds: float | None = None,
) -> IntradayBars:
    """Fetch source bars and fold them to `minutes`-minute bars.

    `range_` defaults to the deepest history the source interval allows —
    asking for more silently returns less, or a 422.
    """
    interval = source_interval(minutes)
    range_ = range_ or max_range(minutes)
    # One bar's worth of staleness at most. A date-stamped cache would happily
    # serve this morning's fetch at three in the afternoon.
    payload = await fetch_payload(
        symbol, client, range_=range_, interval=interval,
        ttl_seconds=minutes * 60 if ttl_seconds is None else ttl_seconds,
    )
    bars = resample(payload, symbol, minutes=minutes, alignment=alignment)
    cache_payload(symbol, range_, interval, payload)
    return bars
