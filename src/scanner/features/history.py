"""Accumulating intraday history past the provider's window.

THE PROBLEM
Yahoo serves 30-minute bars for the last 86 days and nothing before. That
window rolls: what is available today is gone in three months. So the deepest
30-minute backtest possible from a cold start is 86 days — which is exactly
why the one promising result in the setup study rests on the weakest data in
it. The only fix is to keep what you are shown, every day, until the sample
is big enough to mean something.

THE PART THAT WILL CORRUPT YOUR HISTORY IF YOU GET IT WRONG
The most recent bar is still forming. Fetch at 10:15 on a 30-minute series and
the 10:00 bar holds fifteen minutes of trade — a real high, a real low, and
both of them wrong. Store that and it is wrong forever, silently, and it will
manufacture swing pivots that never existed.

Two rules prevent it:

  1. An incomplete bar is never written. A bar is complete only once its
     whole interval is in the past.
  2. A re-fetch overwrites what it overlaps. If a partial bar did slip in, or
     the provider revises one, the newer read wins.

Together those make collection idempotent: running it five times a day and
running it once produce the same store.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from scanner.features.intraday import EXCHANGE_TZ, IntradayBars

STORE_DIR = Path("data/intraday")

# Retention is deliberately expressed in bars, not files or bytes. The newest
# data is the useful data, and a deterministic cap prevents an unattended
# daily collector from growing without bound.
MAX_STORED_BARS = {30: 5_000, 240: 3_000}


def max_stored_bars(minutes: int) -> int:
    """Return the retention cap for an interval."""
    return MAX_STORED_BARS.get(minutes, 5_000)


class HistoryError(RuntimeError):
    """Raised when the accumulated store cannot be read or written."""


def store_path(symbol: str, minutes: int, alignment: str) -> Path:
    """Alignment is part of the key: session- and continuous-aligned bars are
    different series, and mixing them silently produces nonsense structure."""
    safe = symbol.replace("/", "_").replace("^", "IDX_")
    return STORE_DIR / f"{safe}__{minutes}m__{alignment}.json"


def is_complete(start: datetime, minutes: int, now: datetime | None = None) -> bool:
    """A bar is complete once its whole interval has elapsed."""
    now = now or datetime.now(EXCHANGE_TZ)
    return start + timedelta(minutes=minutes) <= now.astimezone(EXCHANGE_TZ)


def _rows(bars: IntradayBars) -> list[list[float]]:
    return [
        [t.timestamp(), o, h, low, c, v]
        for t, o, h, low, c, v in zip(
            bars.times, bars.opens, bars.highs, bars.lows, bars.closes, bars.volumes
        )
    ]


def merge(
    existing: list[list[float]],
    fresh: IntradayBars,
    *,
    now: datetime | None = None,
) -> list[list[float]]:
    """Fold a new fetch into the stored series.

    Newer data wins on overlap; incomplete bars are dropped rather than
    stored. Returns rows sorted oldest first.
    """
    by_stamp: dict[float, list[float]] = {row[0]: row for row in existing}
    for row, start in zip(_rows(fresh), fresh.times):
        if not is_complete(start, fresh.interval_minutes, now):
            continue
        by_stamp[row[0]] = row  # a re-fetch overwrites what it overlaps
    return [by_stamp[k] for k in sorted(by_stamp)]


def load(symbol: str, minutes: int, alignment: str) -> list[list[float]]:
    path = store_path(symbol, minutes, alignment)
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HistoryError(f"{symbol}: accumulated store is corrupt: {exc}") from exc
    if not isinstance(rows, list):
        raise HistoryError(f"{symbol}: accumulated store is not a series")
    return rows


def save(symbol: str, minutes: int, alignment: str, rows: list[list[float]]) -> Path:
    path = store_path(symbol, minutes, alignment)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write beside the target and move: a half-written store read as JSON is
    # a corrupt store, and losing months of collection to one crash is not a
    # risk worth taking for one line of code.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(rows), encoding="utf-8")
    temporary.replace(path)
    return path


def trim(rows: list[list[float]], *, max_bars: int) -> list[list[float]]:
    """Keep the newest complete rows while preserving chronological order."""
    if max_bars <= 0:
        raise HistoryError(f"max_bars must be positive, got {max_bars}")
    return rows[-max_bars:]


def to_bars(
    symbol: str, minutes: int, alignment: str, rows: list[list[float]]
) -> IntradayBars:
    """Rebuild the bar series from stored rows."""
    # Drop the same no-trading placeholders `resample` now refuses to build.
    # Filtered on READ rather than removed from disk: the store is
    # append-only and its value is that it records what the provider actually
    # returned. 2,591 of 250,742 stored bars are placeholders, all written
    # before resample learned to reject them.
    rows = [r for r in rows if not (r[5] <= 0 and r[2] == r[3])]
    if len(rows) < 2:
        raise HistoryError(f"{symbol}: accumulated store has fewer than 2 bars")
    ordered = sorted(rows, key=lambda r: r[0])
    return IntradayBars(
        symbol=symbol,
        interval_minutes=minutes,
        alignment=alignment,
        times=tuple(
            datetime.fromtimestamp(r[0], tz=EXCHANGE_TZ) for r in ordered
        ),
        opens=tuple(r[1] for r in ordered),
        highs=tuple(r[2] for r in ordered),
        lows=tuple(r[3] for r in ordered),
        closes=tuple(r[4] for r in ordered),
        volumes=tuple(r[5] for r in ordered),
    )


def collect(bars: IntradayBars, *, now: datetime | None = None) -> int:
    """Merge a fresh fetch into the store. Returns the stored bar count."""
    rows = merge(
        load(bars.symbol, bars.interval_minutes, bars.alignment), bars, now=now
    )
    rows = trim(rows, max_bars=max_stored_bars(bars.interval_minutes))
    save(bars.symbol, bars.interval_minutes, bars.alignment, rows)
    return len(rows)


def accumulated(
    symbol: str, minutes: int, alignment: str
) -> IntradayBars | None:
    """The full stored series, or None if nothing has been collected yet."""
    rows = load(symbol, minutes, alignment)
    if len(rows) < 2:
        return None
    return to_bars(symbol, minutes, alignment, rows)
