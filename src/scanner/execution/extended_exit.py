"""Closing a position when the exchange is open but stops are not.

WHAT THIS IS FOR
Between 20:00 and 04:00 ET the market trades and this system cannot act. A
bracket's `stop_loss` leg is a stop order, stop orders are regular-hours only,
and `flatten.py` closes with `DELETE /v2/positions`, which is a market order —
also regular-hours only. So for roughly half the positions this strategy holds
overnight by design, there is currently no way to get out at all until 09:30,
by hand or otherwise. This is the missing exit.

WHAT THIS IS NOT
It is not overnight stop protection and it cannot be made into it. Alpaca
accepts **only limit orders** outside regular hours:

    "Only limit orders with time_in_force set to day or gtc orders are
     accepted as extended hours eligible."

and brackets are excluded outright:

    "Extended hours are not supported. extended_hours must be false or
     omitted."

A limit order is not a stop. It does not trigger on a price level, and in a
fast move down it is exactly the order that does not fill. Treat every exit
here as **best effort** and check the result — the script reports an unfilled
exit as a failure rather than reporting the submission as success.

NOT YET VERIFIED AGAINST A LIVE ORDER
The payload below is built from the documented rules and the unit tests pin
its shape, but no order from this module has been accepted by the broker yet.
Verifying it needs an actual position to close, and opening one purely to test
would put a trade inside the evaluation window that the strategy never chose.
So the first real use is also the first proof. Run it with `--symbol` against
a single name, watch the result, and do not assume a submitted order filled —
the script already refuses to make that assumption for you.

IT ONLY EVER CLOSES
Nothing here can open a position. The side of each order is derived from the
sign of a position the broker reports holding, and a flat book produces an
empty plan. That is the same invariant `flatten.py` is pinned on, for the same
reason: this code runs unattended at hours when nobody is watching, and the
worst failure available to it would be opening a short it thinks is an exit.

WHY EXIT-ONLY AND NOT ENTRIES
Entries overnight would need setups, setups need bars, and the bar source here
is Yahoo's regular-hours series — it cannot see 20:00–04:00 at all. Beyond the
data, enabling limit exits *without* stops is asymmetric: the take-profit side
would fire overnight while the stop side still could not, capping winners while
leaving losers running. Closing a position is the only overnight action that is
risk-reducing whichever way price moved.
"""

from __future__ import annotations

from typing import Any, Iterable

from scanner.execution.alpaca import round_tick
from scanner.models import Frozen

#: Order states that still reserve shares and must be cancelled before an exit
#: can use them. A resting bracket leg holds the quantity it would sell, so an
#: exit submitted underneath one is rejected for insufficient shares — the
#: lesson `flatten.py` already learned and is pinned on.
WORKING = frozenset({"new", "accepted", "partially_filled", "pending_new",
                     "accepted_for_bidding", "held", "replaced"})

#: How far through the reference price to place the limit so it is marketable.
#: Overnight books are thin, and a limit resting exactly at the last print is a
#: limit that does not fill. 50bp is a starting point, not a measured value.
DEFAULT_SLIPPAGE_BPS = 50.0

#: Refuse to price an exit off a reference this far from sane. A stale or zero
#: `current_price` overnight would otherwise produce an order at any price.
MIN_REFERENCE = 0.01


class ExitLeg(Frozen):
    """One position, and the order that closes it."""

    symbol: str
    side: str           # sell to close a long, buy to close a short
    shares: int
    reference: float
    limit: float

    def describe(self) -> str:
        return (f"{self.symbol:<6} {self.side:<4} {self.shares:>5} "
                f"@ limit {self.limit:.2f}  (ref {self.reference:.2f})")


class ExitPlan(Frozen):
    """What to cancel, then what to close. Order matters."""

    cancel_first: tuple[str, ...] = ()
    legs: tuple[ExitLeg, ...] = ()
    skipped: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.cancel_first and not self.legs

    def describe(self) -> str:
        if self.is_empty:
            return "nothing to close — no positions, no working orders"
        parts = []
        if self.cancel_first:
            parts.append(f"cancel {len(self.cancel_first)} working order(s)")
        if self.legs:
            parts.append(f"close {len(self.legs)}: "
                         + ", ".join(leg.symbol for leg in self.legs))
        if self.skipped:
            parts.append(f"skipped {len(self.skipped)}: {', '.join(self.skipped)}")
        return "; ".join(parts)


def marketable_limit(side: str, reference: float, slippage_bps: float) -> float:
    """A limit priced through the reference so it can actually trade.

    Selling prices below the reference and buying prices above it. Getting this
    backwards produces an order that rests untouched all night while the
    position it was meant to close keeps moving, which is indistinguishable
    from having sent nothing.
    """
    if reference < MIN_REFERENCE:
        raise ValueError(f"reference price {reference} is not usable")
    if slippage_bps < 0:
        raise ValueError("slippage cannot be negative")
    shift = reference * (slippage_bps / 10_000.0)
    price = reference - shift if side == "sell" else reference + shift
    return round_tick(max(price, MIN_REFERENCE))


def _quantity(position: dict[str, Any]) -> float:
    try:
        return float(position.get("qty") or 0)
    except (TypeError, ValueError):
        return 0.0


def _reference(position: dict[str, Any]) -> float:
    for field in ("current_price", "avg_entry_price"):
        try:
            value = float(position.get(field) or 0)
        except (TypeError, ValueError):
            continue
        if value >= MIN_REFERENCE:
            return value
    return 0.0


def plan_extended_exit(
    positions: Iterable[dict[str, Any]],
    orders: Iterable[dict[str, Any]],
    *,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    only: frozenset[str] | None = None,
) -> ExitPlan:
    """Decide what to cancel and what to close, from the broker's own records.

    Takes raw broker dicts rather than parsed objects so a field this project
    does not model cannot silently drop a position from the plan.

    A position the broker reports without a usable price is **skipped, not
    guessed at**: an exit priced off a zero reference is worse than no exit,
    because it reports success while resting somewhere it will never fill.
    """
    cancel = tuple(
        str(order["id"]) for order in orders
        if str(order.get("status", "")).lower() in WORKING
        and (only is None or str(order.get("symbol", "")).upper() in only)
        and order.get("id")
    )

    legs: list[ExitLeg] = []
    skipped: list[str] = []
    for position in positions:
        symbol = str(position.get("symbol") or "").upper()
        if not symbol or (only is not None and symbol not in only):
            continue
        quantity = _quantity(position)
        if quantity == 0:
            continue
        reference = _reference(position)
        if reference < MIN_REFERENCE:
            skipped.append(f"{symbol} (no usable price)")
            continue
        side = "sell" if quantity > 0 else "buy"
        legs.append(ExitLeg(
            symbol=symbol,
            side=side,
            shares=int(abs(quantity)),
            reference=reference,
            limit=marketable_limit(side, reference, slippage_bps),
        ))

    return ExitPlan(
        cancel_first=cancel,
        legs=tuple(sorted(legs, key=lambda leg: leg.symbol)),
        skipped=tuple(sorted(skipped)),
    )


def exit_payload(leg: ExitLeg) -> dict[str, Any]:
    """The order body for one extended-hours exit.

    `limit` and `extended_hours: true` are both mandatory — the broker rejects
    anything else outside regular hours. `day` rather than `gtc` so an exit
    that does not fill expires instead of resting into a session where the
    price it was chosen for no longer means anything.
    """
    return {
        "symbol": leg.symbol,
        "qty": str(leg.shares),
        "side": leg.side,
        "type": "limit",
        "limit_price": f"{leg.limit:.2f}",
        "time_in_force": "day",
        "extended_hours": True,
    }
