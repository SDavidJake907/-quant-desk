"""When the market is shut, and what that means for an order.

BARS ARE NOT TIME
Every freshness and holding rule in this project counts BARS. Inside a session
that is a fair proxy for elapsed time; across a session boundary it is not.
Overnight is one bar and seventeen and a half hours, and a weekend is one bar
and sixty-five. Rules written in bars silently treat those as the same
distance, which is how a setup from yesterday afternoon reads as fresh three
minutes after today's open.

Both predicates below read the bar TIMESTAMPS rather than a session calendar.
A market closed for a holiday, a half day, or an unscheduled halt produces the
same gap in the timestamps, and what matters is that the exchange was shut
between two prices, not the reason it was.
"""

from __future__ import annotations

from typing import Protocol
from datetime import date, datetime

#: Monday is 0.
FRIDAY = 4

#: Calendar days between two adjacent bars that imply a weekend rather than an
#: overnight. Three catches Friday-to-Monday and every long weekend, without
#: mistaking a single overnight for one.
WEEKEND_DAYS = 3


class Timed(Protocol):
    """Any bar series that knows when its bars are."""

    times: tuple[datetime, ...]

    def __len__(self) -> int: ...


def crosses_session(bars: Timed, index: int) -> bool:
    """Whether the market closed between `index` and the bar after it.

    False on the final bar of the series: there is no later bar to compare
    against, and treating the newest bar as a boundary would refuse every
    setup a live scan is meant to act on.
    """
    if index < 0 or index + 1 >= len(bars):
        return False
    return bars.times[index].date() != bars.times[index + 1].date()


def is_last_bar_before_weekend(bars: Timed, index: int) -> bool:
    """Whether `index` is the final bar before a multi-day close.

    A Friday holiday makes Thursday's last bar the boundary, so the gap is
    measured in days rather than assumed from the weekday alone.
    """
    if index < 0 or index + 1 >= len(bars):
        return False
    here, nxt = bars.times[index], bars.times[index + 1]
    if here.date() == nxt.date():
        return False
    if (nxt.date() - here.date()).days >= WEEKEND_DAYS:
        return True
    return here.weekday() == FRIDAY and nxt.date() != here.date()


def bars_until_weekend(bars: Timed, index: int) -> int | None:
    """Bars from `index` to the last one before the market shuts for the
    weekend, or None if the series ends first.

    Used to cap a holding period: passing this as `max_bars` makes the
    resolver's existing time-limit path do the exit, so a flattened trade is
    scored exactly like any other timeout and there is no second exit path to
    keep correct.
    """
    for i in range(max(index, 0), len(bars) - 1):
        if is_last_bar_before_weekend(bars, i):
            return i - index
    return None


def is_stale_session(bars: Timed, index: int, *, session: date) -> bool:
    """Whether bar `index` belongs to an earlier session than `session`.

    WHY `crosses_session` IS NOT ENOUGH ON A LIVE SCAN
    That predicate asks whether the market closed between bar i and bar i+1,
    which needs an i+1 to exist. Live, the still-forming bar used to supply
    one — so a setup on yesterday's final bar was refused. `drop_incomplete`
    then removed the partial bar, correctly, and yesterday's final bar became
    the last in the series. With no successor the guard returned False and the
    setup read age 0, fresh and tradeable: the EXPE entry, eighteen hours after
    its trigger, would have been possible again.

    Two fixes, each right on its own, reopening between them the hole either
    was meant to close.

    The question a live scan actually needs is not about a later bar at all. It
    is whether this bar belongs to the session being traded, and that cannot be
    read off the array — so the session is passed in. Both predicates are
    checked together at the call site; each catches a case the other does not.
    """
    if index < 0 or index >= len(bars):
        return False
    return bars.times[index].date() != session
