"""Paper orders for the momentum engine (WORKLIST §37).

The dry run (`dry_run_book.json`) keeps running unchanged as the engine's
clean record. The PAPER book is separate state, sized against the momentum
sleeve, and its trades reach the broker as market-on-close orders -- the
backtest entered at the close.

TWO STRATEGIES MUST NEVER SHARE A POSITION
Alpaca nets every buy of a symbol into one position. If momentum bought a name
a manual pick already holds, the manual bracket's legs would reserve shares
momentum thinks it owns, and the ownership map would hand the manual stop to
momentum. So a pick held or ordered by anyone else is dropped BEFORE the
tranche is built, and the tranche is equal-weighted across what remains.

IDEMPOTENT BY CLIENT ID
`mom-{SYMBOL}-{YYYYMM}-{side}` is the same on every retry within a month, and
Alpaca refuses a reused client_order_id. A rerun after a partial failure can
therefore never double an order; the refusal is read as "already sent".

PURE. Nothing here reaches a broker.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from scanner.execution.ownership import MOMENTUM, prefix_for


def drop_foreign(picks: Iterable[str], foreign: Iterable[str]) -> tuple[str, ...]:
    """Picks not held or ordered by another strategy, rank order kept."""
    taken = set(foreign)
    return tuple(p for p in picks if p not in taken)


def client_order_id(symbol: str, side: str, day: date) -> str:
    return f"{prefix_for(MOMENTUM)}{symbol}-{day:%Y%m}-{side}"


def moc_payload(symbol: str, side: str, qty: int, day: date) -> dict[str, Any]:
    """Market-on-close. Alpaca requires MOC orders before 15:50 ET."""
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be buy or sell, not {side!r}")
    if qty < 1:
        raise ValueError(f"{symbol}: quantity {qty} is not an order")
    return {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "cls",
        "client_order_id": client_order_id(symbol, side, day),
    }


def clamp_sells(trades: Iterable[tuple[str, str, int]],
                held: Mapping[str, int]) -> tuple[tuple[str, str, int], ...]:
    """Never sell more than the broker says momentum holds; a sell of a name
    no longer held (stopped by hand, delisted) becomes no order at all."""
    out = []
    for symbol, side, qty in trades:
        if side == "sell":
            qty = min(qty, int(held.get(symbol, 0)))
            if qty < 1:
                continue
        out.append((symbol, side, qty))
    return tuple(out)


def is_duplicate(error: str) -> bool:
    """Alpaca's refusal of a reused client_order_id."""
    text = error.lower()
    return "client_order_id" in text and ("unique" in text or "duplicate" in text)
