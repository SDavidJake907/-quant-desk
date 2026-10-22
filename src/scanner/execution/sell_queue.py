"""Operator sells that wait for a price (operator 2026-10-22: "sell MU and NUE now / next price").

After hours a sale needs a fresh trade to price its limit order; a thin name can go
an hour without one. data/monitor/sell_queue.json holds {symbol: reason}; peak
watch tries every queued symbol each pass (every 5 min, 24/5) until it is no longer
held. A queued symbol with a sell limit or market order already working is left to
it -- its stop does not count as a sale.

PURE: dicts in, new values out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

PATH = "data/monitor/sell_queue.json"
WORKING = frozenset({"new", "accepted", "held", "partially_filled", "pending_new",
                     "accepted_for_bidding", "pending_replace"})


def _selling(open_orders: Iterable[Mapping[str, Any]]) -> set[str]:
    """Symbols with a plain sell working. An OCO or bracket order's limit half is the
    position's protection (its target), not a sale."""
    return {str(o.get("symbol")) for o in open_orders
            if o.get("side") == "sell" and o.get("type") in ("limit", "market")
            and o.get("status") in WORKING
            and o.get("order_class") in (None, "", "simple") and not o.get("legs")}


def due(queue: Mapping[str, str], *, held: Iterable[str],
        open_orders: Iterable[Mapping[str, Any]]) -> list[str]:
    """Queued symbols still held with no sale working, in queue order."""
    keep, busy = set(held), _selling(open_orders)
    return [s for s in queue if s in keep and s not in busy]


def prune(queue: Mapping[str, str], *, held: Iterable[str]) -> dict[str, str]:
    keep = set(held)
    return {s: why for s, why in queue.items() if s in keep}
