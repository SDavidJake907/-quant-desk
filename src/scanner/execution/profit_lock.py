"""Profit lock: once a position has been green, it never turns into a loss.

Operator 2026-10-22: "i dont think gemma should be hold postion while red ...
sell above price bought it at, so this never go in the red, that the correct
way to secure profit"; chose lock at +1%, trail 2% under the high, both books.

  LOCK   once the high since entry is LOCK_AT_PCT over the entry, the stop moves
         to LOCK_ABOVE_PCT over the entry -- the worst case is a small win
  TRAIL  then the stop follows the high at TRAIL_PCT under it, never down
  LOSS   until then, the stop is never more than MAX_LOSS_PCT under the entry
         (operator 2026-10-23, after RNG -7.8%: "fix that under where we bought
         this at" -- winners were +$31 each, losers -$152; ATR stops sat 5-15% down)

scripts/peak_watch.py runs it every 5 minutes, 24/5: in any session it raises
the broker's stop (PATCH, so a gap at the open still sells), and when the price
is already at or under the lock it sells itself -- after hours Alpaca holds no
stops. The high is tracked from the first pass that sees the position (its 5-
minute price), never back-filled from older bars, so a lock never fires on
history it did not watch. An operator rule, not a tested edge (FREEZE.md).

PURE: numbers and order dicts in, new values out; nothing is mutated.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: 3 / 1 / 3 from 2026-10-23 (was 1 / 0.2 / 2): the old lock sold winners on normal
#: wiggle at +0.2-0.6% (operator + Gemma: let winners run, still catch the top).
LOCK_AT_PCT = 3.0
LOCK_ABOVE_PCT = 1.0
TRAIL_PCT = 3.0
MAX_LOSS_PCT = 3.0
#: Working order statuses a stop can be in (a bracket's stop leg sits "held").
LIVE = frozenset({"new", "accepted", "held", "partially_filled", "pending_new",
                  "accepted_for_bidding", "pending_replace"})
#: Raise the broker stop only when the lock is at least this far above it.
MIN_RAISE = 0.01

State = Mapping[str, Mapping[str, float]]


def loss_floor(entry: float) -> float:
    return entry * (1 - MAX_LOSS_PCT / 100)


def lock_stop(*, entry: float, high: float) -> float:
    """The loss floor (MAX_LOSS_PCT under the entry) until the position has been up
    LOCK_AT_PCT; then the lock/trail stop."""
    if high < entry * (1 + LOCK_AT_PCT / 100):
        return loss_floor(entry)
    return max(entry * (1 + LOCK_ABOVE_PCT / 100), high * (1 - TRAIL_PCT / 100))


def update(state: State, symbol: str, *, entry: float, price: float) -> dict[str, dict[str, float]]:
    """New state with this symbol's high raised to `price`; a changed entry starts over."""
    out = {s: dict(v) for s, v in state.items()}
    seen = out.get(symbol)
    if seen is None or abs(seen["entry"] - entry) > 1e-6:
        out[symbol] = {"entry": entry, "high": price}
    else:
        out[symbol] = {"entry": entry, "high": max(seen["high"], price)}
    return out


def prune(state: State, held: Iterable[str]) -> dict[str, dict[str, float]]:
    keep = set(held)
    return {s: dict(v) for s, v in state.items() if s in keep}


def stop_orders(orders: Iterable[Mapping[str, Any]]) -> dict[str, tuple[str, float]]:
    """symbol -> (order id, stop price) of its working protective stop, legs included."""
    out: dict[str, tuple[str, float]] = {}

    def walk(o: Mapping[str, Any]) -> None:
        if (o.get("side") == "sell" and o.get("type") in ("stop", "stop_limit")
                and o.get("status") in LIVE and o.get("stop_price") and o.get("id")):
            out.setdefault(str(o["symbol"]), (str(o["id"]), float(o["stop_price"])))
        for leg in o.get("legs") or []:
            walk(leg)

    for o in orders:
        walk(o)
    return out


def action(*, price: float, lock: float | None, broker_stop: float | None) -> str:
    """'sell' (price at/under the lock), 'raise' (move the broker stop up) or 'none'."""
    if lock is None:
        return "none"
    if price <= lock:
        return "sell"
    if broker_stop is None or lock >= broker_stop + MIN_RAISE:
        return "raise"
    return "none"
