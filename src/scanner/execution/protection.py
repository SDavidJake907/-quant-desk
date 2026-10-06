"""What to do with an open position that has no live stop.

THE GAP THIS CLOSES (2026-10-06)
protect.py used to re-arm a naked position only if the price was still inside
its bracket, and to REPORT a breached stop "for a human to close". In an
unattended system no human comes. On 2026-10-05 INTC sat with no stop from
05:33 to 06:03 AK because its price was under the stop; it was saved only
because the price ticked back above and the next run re-armed it.

The stop was the strategy's decision, made when the trade was sized. If price
is already through it, the exit it calls for is a sale at market now -- not a
stop re-armed above the market, and not nothing. Same for a reached target.
Outside market hours a day market order is queued for the next open, which is
exactly what a stop does on a gap. Every action is written to an alert file
so a person still finds out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

#: Order statuses that are still working (alpaca.LIVE_STATUSES, minus nothing).
WORKING = frozenset({"new", "accepted", "held", "partially_filled", "pending_new",
                     "accepted_for_bidding", "pending_replace"})


def decide(*, last: float, stop: float | None, target: float | None) -> str:
    """'arm', 'close_stop', 'close_target' or 'unknown' (no recorded levels)."""
    if stop is None or target is None:
        return "unknown"
    if last and last <= stop:
        return "close_stop"
    if last and last >= target:
        return "close_target"
    return "arm"


def exit_payload(symbol: str, qty: int, at: datetime) -> dict[str, Any]:
    """A plain market sell for the naked shares only -- shares already behind a
    working stop are left to it."""
    return {
        "symbol": symbol, "qty": str(qty), "side": "sell", "type": "market",
        "time_in_force": "day",
        "client_order_id": f"protect-close-{symbol}-{at:%Y%m%dT%H%M%S}",
    }


def closing_shares(orders: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Shares already being sold by a working market order, per symbol, so a
    later run does not send a second close for the same position."""
    out: dict[str, int] = {}
    for order in orders:
        if (order.get("side") == "sell" and order.get("type") == "market"
                and str(order.get("status", "")) in WORKING):
            symbol = str(order.get("symbol", ""))
            out[symbol] = out.get(symbol, 0) + int(float(order.get("qty") or 0))
    return out


def alert_text(rows: Iterable[tuple[str, int, str, str]], *, when: str) -> str:
    """rows: (symbol, shares, decision, what happened)."""
    lines = [f"PROTECTION ALERT -- {when}", "",
             "A position was found with no live stop:"]
    lines += [f"  {symbol:<6} {qty:>6} sh  {decision:<13} {result}"
              for symbol, qty, decision, result in rows]
    lines += ["", "close_stop / close_target: a market sell was sent (queued for the",
              "open if the market was shut). unknown: no stop on record -- act by hand.",
              "Check the broker; then delete this file."]
    return "\n".join(lines) + "\n"
