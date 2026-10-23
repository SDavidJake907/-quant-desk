"""Opening-range breakout day book: the rules (operator 2026-10-23, "lets tie this in").

The idea is Lumibot's ORB example, written here in our own order path. Gemma
was asked first; she agreed to give it 10% of her share and asked for three
changes, all below: 2x opening volume, the breakout bar above its 9-period
EMA, and a 2% stop when the range is very tight.

  9:45 ET   from Gemma's saved pool (advisor/gemma_pool.save), keep the names
            whose first 15 minutes traded >= MIN_RVOL x their usual first 15
            minutes, with a range no wider than MAX_RANGE_ATR daily ATRs, no
            earnings today, held by no book. Strongest volume first.
  9:45 to   every 5 minutes: a completed 5-minute close above the range high
  close-30  and above the 9-period EMA buys (limit, bracket). Stop = the range
            low (or TIGHT_STOP_PCT under the buy when the range is tighter
            than TIGHT_RANGE_ATR ATRs), never more than profit_lock's 3% under
            the buy. Target = REWARD_RISK x the risk. One trade per name a day.
  close-10  everything this book holds is sold. It never holds overnight.

Rules, not judgement: Gemma is told each evening what it did, nothing more.
CHOSEN, not measured. PURE: numbers in, decisions out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta

from scanner.execution import profit_lock
from scanner.execution.alpaca import round_tick
from scanner.models import Frozen

OR_MINUTES = 15
BASELINE_DAYS = 10
#: Fewer usable baseline days than this and the name is skipped (new listing, gaps).
MIN_BASELINE_DAYS = 5
MIN_RVOL = 2.0
MAX_RANGE_ATR = 1.0
TIGHT_RANGE_ATR = 0.3
TIGHT_STOP_PCT = 2.0
EMA_PERIOD = 9
#: At most this many names are watched for a break each day.
SHORTLIST_MAX = 30
#: A close further than this many range-widths above the high is a chase, not a break.
MAX_EXTENSION_RANGES = 1.0
LAST_ENTRY_BEFORE_CLOSE = timedelta(minutes=30)
FLAT_BEFORE_CLOSE = timedelta(minutes=10)
#: An unfilled entry older than this is cancelled: the break has moved on.
STALE_ENTRY_MINUTES = 10.0

WAIT, ENTER, MANAGE, FLATTEN = "wait", "enter", "manage", "flatten"


class Range(Frozen):
    symbol: str
    high: float
    low: float
    volume: float
    rvol: float
    atr_pct: float
    range_atr: float


def phase(now: datetime, open_at: datetime, close_at: datetime) -> str:
    """What the book may do at `now` in a session from `open_at` to `close_at`."""
    if now >= close_at - FLAT_BEFORE_CLOSE:
        return FLATTEN
    if now < open_at + timedelta(minutes=OR_MINUTES):
        return WAIT
    if now >= close_at - LAST_ENTRY_BEFORE_CLOSE:
        return MANAGE
    return ENTER


def opening_range(symbol: str, *, high: float, low: float, close: float, volume: float,
                  usual_volume: float | None, atr_pct: float | None) -> Range | None:
    """The first 15 minutes, measured against the stock's usual. None if unmeasurable."""
    if not usual_volume or usual_volume <= 0 or not atr_pct or atr_pct <= 0:
        return None
    if close <= 0 or high <= low:
        return None
    span_pct = (high - low) / close * 100
    return Range(symbol=symbol, high=high, low=low, volume=volume,
                 rvol=round(volume / usual_volume, 2), atr_pct=atr_pct,
                 range_atr=round(span_pct / atr_pct, 3))


def usual_volume(volumes: Sequence[float]) -> float | None:
    """Mean first-15-minute volume over the baseline days; None when too few."""
    usable = [v for v in volumes if v and v > 0]
    return sum(usable) / len(usable) if len(usable) >= MIN_BASELINE_DAYS else None


def shortlist(ranges: Iterable[Range], *, exclude: Iterable[str] = ()) -> list[Range]:
    """Names moving today, most unusual volume first."""
    skip = set(exclude)
    kept = [r for r in ranges if r.symbol not in skip and r.rvol >= MIN_RVOL
            and r.range_atr <= MAX_RANGE_ATR]
    return sorted(kept, key=lambda r: (-r.rvol, r.symbol))[:SHORTLIST_MAX]


def ema(closes: Sequence[float], period: int = EMA_PERIOD) -> float | None:
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    value = sum(closes[:period]) / period
    for c in closes[period:]:
        value = c * k + value * (1 - k)
    return value


def breakout(rng: Range, closes: Sequence[float]) -> bool:
    """The last completed 5-minute close is above the range high, above its EMA,
    and not more than MAX_EXTENSION_RANGES range-widths past the high."""
    if not closes:
        return False
    last = closes[-1]
    average = ema(closes)
    ceiling = rng.high + MAX_EXTENSION_RANGES * (rng.high - rng.low)
    return average is not None and rng.high < last <= ceiling and last > average


def levels(rng: Range, last: float, *, offset_pct: float,
           reward_risk: float) -> tuple[float, float, float]:
    """(limit, stop, target) for a buy at `last`."""
    limit = round_tick(last * (1 + offset_pct / 100))
    if rng.range_atr < TIGHT_RANGE_ATR:
        stop = limit * (1 - TIGHT_STOP_PCT / 100)    # Gemma: tight range, hard 2%
    else:
        stop = rng.low
    stop = round_tick(max(stop, profit_lock.loss_floor(limit)))
    target = round_tick(limit + reward_risk * (limit - stop))
    return limit, stop, target


def day_pnl(fills: Iterable[Mapping[str, object]], symbols: Iterable[str]) -> dict[str, float]:
    """Realised dollars per symbol from the day's FILL activities: sells minus buys.
    A symbol still held (more bought than sold) is left out."""
    wanted = set(symbols)
    cash: dict[str, float] = {}
    shares: dict[str, float] = {}
    for f in fills:
        s = str(f.get("symbol") or "")
        if s not in wanted:
            continue
        qty = float(f.get("qty") or 0)
        px = float(f.get("price") or 0)
        sign = 1 if str(f.get("side")) == "sell" else -1
        cash[s] = cash.get(s, 0.0) + sign * qty * px
        shares[s] = shares.get(s, 0.0) - sign * qty
    return {s: round(v, 2) for s, v in cash.items() if abs(shares.get(s, 0.0)) < 1e-9}


def report(day: str, pnl: Mapping[str, float], *, running: float, days: int,
           shortlisted: int) -> str:
    """The evening line Gemma is shown (operator notes)."""
    if not pnl:
        return (f"Breakout book (rules, not yours; 10% of the account) {day}: no trades "
                f"({shortlisted} names watched). Running total ${running:+,.0f} over {days} day(s).")
    trades = ", ".join(f"{s} ${v:+,.0f}" for s, v in sorted(pnl.items()))
    return (f"Breakout book (rules, not yours; 10% of the account) {day}: {trades}; "
            f"day ${sum(pnl.values()):+,.0f}. Running total ${running:+,.0f} over {days} day(s). "
            "It is flat every night.")
