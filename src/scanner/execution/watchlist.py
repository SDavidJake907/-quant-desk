"""Setups deferred for a reason that will expire.

THE DISTINCTION THAT MAKES THIS WORTH HAVING
Refusals are not all the same shape. Some are permanent and some are merely
"not yet", and treating them alike loses trades for no reason:

    permanent   the company is being acquired — its price tracks an exchange
                ratio and will not represent its own business again while the
                deal lives. There is nothing to wait for.

    temporary   earnings land on Tuesday; the position limit is full; buying
                power is short. The setup is fine. The timing is not.

A permanent refusal is recorded and dropped. A temporary one belongs here,
with the date it becomes eligible again, so it returns on its own instead of
depending on somebody remembering.

WHAT IT DELIBERATELY DOES NOT DO
It does not re-detect. A watch-listed setup carries the levels computed at its
trigger bar, and those go stale: a stop that made sense on Tuesday describes a
swing low the market may have long since taken out. So an entry expires after
`max_age_bars` and the scanner has to find it again. Waiting for a better
price would be a different entry rule and would need its own holdout — this
only defers, it never re-prices.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from scanner.models import Frozen

STORE = Path("data/watchlist.json")

#: A deferred setup older than this is dropped rather than revived. Its levels
#: were computed from a swing that may no longer exist.
DEFAULT_MAX_AGE_BARS = 12


class Watched(Frozen):
    """One setup waiting for its blocker to clear."""

    symbol: str
    reason: str
    entry_price: float
    stop_price: float
    target_price: float
    trigger_at: str
    added_at: str
    eligible_on: str = ""     # ISO date; empty means "retry next run"

    def eligible(self, today: date | None = None) -> bool:
        if not self.eligible_on:
            return True
        try:
            return date.fromisoformat(self.eligible_on) <= (today or date.today())
        except ValueError:
            return True

    @property
    def waiting_for(self) -> str:
        return f"{self.reason} (until {self.eligible_on})" if self.eligible_on else self.reason


class WatchlistError(RuntimeError):
    """Raised when the store cannot be read."""


def load(path: Path | str = STORE) -> tuple[Watched, ...]:
    target = Path(path)
    if not target.exists():
        return ()
    try:
        rows = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WatchlistError(f"watchlist is corrupt: {exc}") from exc
    return tuple(Watched(**row) for row in rows if isinstance(row, dict))


def save(entries: tuple[Watched, ...], path: Path | str = STORE) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps([e.model_dump(mode="json") for e in entries], indent=1),
        encoding="utf-8",
    )
    temporary.replace(target)


def defer(
    entries: tuple[Watched, ...], entry: Watched
) -> tuple[Watched, ...]:
    """Add or refresh one deferred setup. Keyed by symbol and trigger bar, so
    re-scanning the same setup updates rather than duplicates it."""
    key = (entry.symbol, entry.trigger_at)
    kept = tuple(e for e in entries if (e.symbol, e.trigger_at) != key)
    return kept + (entry,)


def release(
    entries: tuple[Watched, ...],
    *,
    today: date | None = None,
    now: datetime | None = None,
    max_age_hours: float = 48.0,
) -> tuple[tuple[Watched, ...], tuple[Watched, ...]]:
    """Split into (ready to reconsider, still waiting).

    Stale entries are dropped from both: their levels came from a swing that
    may no longer exist, and reviving them would trade a setup nobody has
    re-detected.
    """
    now = now or datetime.now().astimezone()
    ready, waiting = [], []
    for entry in entries:
        try:
            added = datetime.fromisoformat(entry.added_at)
            age_hours = (now - added).total_seconds() / 3600.0
        except (ValueError, TypeError):
            age_hours = 0.0
        if age_hours > max_age_hours:
            continue  # too old to revive; the scanner must find it again
        (ready if entry.eligible(today) else waiting).append(entry)
    return tuple(ready), tuple(waiting)
