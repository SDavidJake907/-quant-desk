"""Going flat before the weekend.

WHAT THIS BUYS, AND WHAT IT COSTS
A stop is an instruction to an exchange that is shut from Friday afternoon to
Monday morning. Anything that happens in between arrives as a gap through the
stop rather than a fill at it, and there is no way to react. Measured on this
strategy, closing out before the weekend costs -0.213R per trade against a
validated edge of +0.218R -- so this is expensive, and it is a risk decision
rather than an edge one. It is off unless the policy asks for it.

TWO PARTS, BECAUSE ONE IS NOT ENOUGH
Flattening on Friday alone still lets a position be opened on Friday morning
that has hours to go wrong. So the rule has a wind-down: from a chosen weekday
onward no new positions are opened, and before the final close everything left
is shut.

ORDER OF OPERATIONS, WHICH IS NOT ARBITRARY
Cancel the protective legs first, then close. A resting stop or limit reserves
the shares behind it, so a close submitted while a bracket is live can be
rejected for insufficient quantity -- and a rejected close looks a lot like
nothing happening. Working entry orders are cancelled too: an unfilled limit
left resting over the weekend can fill on Monday into a book that is supposed
to be empty.

This module is PURE. It decides what should happen and nothing here reaches a
broker, so the plan can be printed and inspected before any order is sent.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from scanner.models import Frozen

#: Order states that are still live and therefore still cancellable.
WORKING = frozenset({"new", "accepted", "partially_filled", "pending_new",
                     "accepted_for_bidding", "held", "replaced"})

#: Monday is 0, so Thursday is 3.
THURSDAY = 3


class FlattenPlan(Frozen):
    """What to cancel and what to close, in that order."""

    cancel_first: tuple[str, ...] = ()
    close: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.cancel_first and not self.close

    def describe(self) -> str:
        if self.is_empty:
            return "nothing to flatten — no positions, no working orders"
        parts = []
        if self.cancel_first:
            parts.append(f"cancel {len(self.cancel_first)} working order(s)")
        if self.close:
            parts.append(f"close {len(self.close)}: {', '.join(self.close)}")
        return "; ".join(parts)


def should_wind_down(on: date, *, from_weekday: int = THURSDAY) -> bool:
    """Whether new positions should stop being opened on `on`.

    Weekday rather than a date rule so a short week behaves sensibly: the point
    is proximity to the weekend, and Thursday is Thursday whatever the month.
    """
    return on.weekday() >= from_weekday


def plan_flatten(*, positions, orders) -> FlattenPlan:
    """Decide what to cancel and close from raw broker records.

    Takes the broker's own dicts rather than parsed objects so that a field
    this project does not model cannot silently drop a position from the plan.
    """
    cancel = tuple(
        str(order["id"]) for order in orders
        if str(order.get("status", "")).lower() in WORKING
    )
    close: list[str] = []
    for position in positions:
        try:
            quantity = float(position.get("qty") or 0)
        except (TypeError, ValueError):
            quantity = 0.0
        symbol = str(position.get("symbol") or "")
        if symbol and quantity != 0 and symbol not in close:
            close.append(symbol)
    return FlattenPlan(cancel_first=cancel, close=tuple(close))


def is_flat(*, positions, orders) -> bool:
    """Whether the account holds nothing and has nothing working.

    Read from the BROKER's own records, never from what this process believes
    it did. A close that was rejected, partially filled, or silently dropped
    looks identical to a successful one from the sending side.

    A resting entry order counts against flatness: unfilled, it can fill on
    Monday morning into a book that is supposed to be empty.
    """
    for position in positions:
        try:
            if float(position.get("qty") or 0) != 0:
                return False
        except (TypeError, ValueError):
            return False          # unparseable quantity is not proof of flat
    return not any(
        str(order.get("status", "")).lower() in WORKING for order in orders
    )


def held_back(positions, symbols) -> tuple[str, ...]:
    """Which of `symbols` still have shares reserved by a working order.

    A cancel is accepted before it is processed. Until it is, the cancelled
    leg's shares stay in `held_for_orders` and a close is refused for
    insufficient quantity -- which is how INTC went into the weekend of
    2026-10-02 open. A position missing `qty_available` is treated as held:
    absence of the field is not evidence the shares are free.
    """
    wanted = set(symbols)
    held: list[str] = []
    for position in positions:
        symbol = str(position.get("symbol") or "")
        if symbol not in wanted:
            continue
        try:
            free = abs(float(position["qty_available"]))
            total = abs(float(position.get("qty") or 0))
        except (KeyError, TypeError, ValueError):
            held.append(symbol)
            continue
        if free < total:
            held.append(symbol)
    return tuple(held)


def closing_within(now: datetime, *, close_at: datetime | None,
                   minutes: int) -> bool:
    """Whether the session ends within `minutes` from `now`.

    THE CASE THIS EXISTS FOR
    US markets close at 13:00 ET on a handful of days — the Friday after
    Thanksgiving, the sessions around Independence Day. A task pinned to 15:45
    ET fires nearly three hours AFTER the bell on those days, finds the market
    shut and cannot flatten anything. So the session end comes from the broker
    calendar and the job decides for itself whether it is the run that should
    act.

    Already closed is deliberately NOT a closing window. It is a different
    state — nothing can be flattened after the bell — and treating it as one
    would let a too-late run report success by doing nothing.
    """
    if close_at is None:
        return False
    remaining = close_at - now
    return timedelta(0) <= remaining <= timedelta(minutes=minutes)


#: Calendar days from one session to the next that count as a weekend-length
#: closure. Friday -> Monday is 3; a one-day midweek holiday is 2 and does not.
WEEKEND_GAP_DAYS = 3


def closes_for_weekend(today, next_session) -> bool:
    """Whether today's session is the last before a closure of 3+ days.

    The flatten used to be scheduled on Fridays only, so when Friday was a
    market holiday the last session was Thursday and nothing flattened:
    2026-12-24 and 12-31, 2027-03-25 and 06-17 (FREEZE.md). Asking the
    calendar for the NEXT session covers every such week, and an ordinary
    Friday too. An unknown next session errs toward flattening -- a failed
    calendar read must not leave positions over a weekend.
    """
    if next_session is None:
        return True
    return (next_session - today).days >= WEEKEND_GAP_DAYS


def flatten_alert_text(stuck, *, working_orders: bool, when: str) -> str:
    """The note left on the Desktop when the flatten could not get flat."""
    lines = [f"WEEKEND FLATTEN FAILED -- {when}", "",
             "Still open going into a market closure:"]
    lines += [f"  {s}" for s in stuck] or ["  (no positions)"]
    if working_orders:
        lines.append("  working orders are still live")
    lines += ["", "Close these by hand before the bell:",
              "  python scripts/flatten.py --submit",
              "or in the Alpaca dashboard. Delete this file once flat."]
    return "\n".join(lines) + "\n"
