"""Which days the exchange is shut, and which session is the last one before it.

THIS FILE DOES NOT CONTAIN A LIST OF HOLIDAYS, ON PURPOSE
A hardcoded holiday table is a second source of truth that is correct on the
day it is written and silently wrong afterwards. Exchanges add closures for
national days of mourning, move early closes when a holiday lands on a
weekend, and publish the following year late. Every function here reads the
broker's own `/v2/calendar` payload, which is the same source the live flatten
already trusts to find today's closing bell.

What this module adds is the question nothing currently asks: **is today the
last session before a multi-day close?** `execution/session.py` answers it from
bar timestamps, which only exist after the close has happened. That is fine for
a backtest resolving a finished series and useless to a live scan deciding
whether to open a position this afternoon.

The gap it describes is real and dated. `wind_down_from_weekday` stops entries
on Friday, and both flatten triggers are registered Friday-only, so a week
whose Friday is a holiday takes entries on Thursday and never flattens them.
See `FREEZE.md` and `WORKLIST.md` §10.

NOT IMPORTED BY THE TRADING PATH
Nothing under the frozen file list imports this module, so adding it changes no
live behaviour. It exists so the fix can be built and tested against real
dates, and so `scripts/market_calendar.py` can report the closures a human has
to cover by hand while the evaluation window is open.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

from scanner.execution.session import FRIDAY, WEEKEND_DAYS
from scanner.models import Frozen

#: A normal US equity close. Anything earlier is a half day.
REGULAR_CLOSE = time(16, 0)

#: Alpaca omits the fields entirely on a day it considers standard.
DEFAULT_OPEN = "09:30"
DEFAULT_CLOSE = "16:00"


class Session(Frozen):
    """One trading day as the broker describes it."""

    on: date
    opens: time
    closes: time

    @property
    def is_early_close(self) -> bool:
        return self.closes < REGULAR_CLOSE

    @property
    def label(self) -> str:
        early = "  EARLY CLOSE" if self.is_early_close else ""
        return f"{self.on:%a %Y-%m-%d} {self.opens:%H:%M}-{self.closes:%H:%M}{early}"


class Closure(Frozen):
    """A stretch of calendar days with no session in it.

    `last_session` is the day a position would have to survive from, which is
    the one that matters: it is the day entries should stop and the book should
    be flattened.
    """

    last_session: date
    reopens: date
    closes: time

    @property
    def days(self) -> int:
        return (self.reopens - self.last_session).days

    @property
    def is_early_close(self) -> bool:
        return self.closes < REGULAR_CLOSE

    @property
    def label(self) -> str:
        early = ", early close" if self.is_early_close else ""
        return (f"{self.last_session:%a %Y-%m-%d} close {self.closes:%H:%M}"
                f" -> reopens {self.reopens:%a %Y-%m-%d}"
                f" ({self.days}d{early})")


def _parse_time(raw: Any, fallback: str) -> time:
    text = str(raw or fallback).strip() or fallback
    return datetime.strptime(text, "%H:%M").time()


def parse_sessions(payload: Iterable[dict[str, Any]] | None) -> tuple[Session, ...]:
    """Pure parse of a `/v2/calendar` response, sorted and deduplicated.

    A row without a usable date is dropped rather than guessed at. A row
    missing its hours is assumed standard, which is how the broker represents
    an ordinary day.
    """
    seen: dict[date, Session] = {}
    for row in payload or ():
        try:
            on = date.fromisoformat(str(row.get("date") or "").strip())
        except ValueError:
            continue
        try:
            opens = _parse_time(row.get("open"), DEFAULT_OPEN)
            closes = _parse_time(row.get("close"), DEFAULT_CLOSE)
        except ValueError:
            continue
        seen[on] = Session(on=on, opens=opens, closes=closes)
    return tuple(seen[key] for key in sorted(seen))


def holidays(sessions: Iterable[Session], start: date, end: date) -> tuple[date, ...]:
    """Weekdays between `start` and `end` with no session.

    Weekends are not holidays and are not reported. The range is inclusive, and
    a range extending past the calendar that was fetched will report every
    unfetched weekday as a holiday — ask for sessions covering the range you
    intend to ask about.
    """
    trading = {session.on for session in sessions}
    found: list[date] = []
    day = start
    while day <= end:
        if day.weekday() < 5 and day not in trading:
            found.append(day)
        day += timedelta(days=1)
    return tuple(found)


def closures(
    sessions: Iterable[Session], *, min_days: int = WEEKEND_DAYS,
) -> tuple[Closure, ...]:
    """Every gap of `min_days` or more between consecutive sessions.

    The default catches ordinary weekends as well as long ones, because a
    weekend is exactly the thing the flatten exists for and treating it as a
    special case is how the holiday version got missed.
    """
    ordered = sorted(sessions, key=lambda session: session.on)
    found: list[Closure] = []
    for current, following in zip(ordered, ordered[1:]):
        if (following.on - current.on).days >= min_days:
            found.append(Closure(
                last_session=current.on,
                reopens=following.on,
                closes=current.closes,
            ))
    return tuple(found)


def unflattened(
    sessions: Iterable[Session], *, flatten_weekday: int = FRIDAY,
    min_days: int = WEEKEND_DAYS,
) -> tuple[Closure, ...]:
    """Closures the Friday-only flatten will not cover.

    A closure is covered only when its last session falls on the weekday the
    flatten is registered for. Every other one leaves the book open across the
    gap, and is a date a human has to flatten by hand until `WORKLIST.md` §10
    lands.
    """
    return tuple(
        closure for closure in closures(sessions, min_days=min_days)
        if closure.last_session.weekday() != flatten_weekday
    )
