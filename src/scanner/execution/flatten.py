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

from datetime import date

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
