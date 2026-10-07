"""The operator's own picks, sent through the same machinery as the scanner.

    python scripts/manual_trade.py NVDA --stop 172.50 --target 195

WHY THROUGH THE SYSTEM AND NOT THE ALPACA DASHBOARD (WORKLIST §37)
A trade placed by hand has no audit record, so protect.py cannot re-arm its
stop, the scorecard cannot mark it, and nobody can later say whether the picks
beat the scanner. Sent from here it gets the same sizing, the same guards, a
GTC bracket, and a `man-` client_order_id that tells every closer whose exit
it is.

PURE. Validation, levels, the sector cap and the order body; the script does
the broker calls.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Any, Iterable, Mapping

from scanner.execution.alpaca import round_tick
from scanner.execution.guards import ALLOWED, Verdict
from scanner.execution.ownership import prefix_for, MANUAL

#: Plain US equity tickers, with an optional class suffix (BRK.B).
_TICKER = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


class ManualError(ValueError):
    """An order that must not be sent, with the reason in plain words."""


def validate_symbol(raw: str) -> str:
    symbol = (raw or "").strip().upper()
    if not _TICKER.match(symbol):
        raise ManualError(f"{raw!r} is not a US stock ticker "
                          "(stocks only -- no crypto pairs, options or futures)")
    return symbol


def resolve_levels(entry: float, stop: float, target: float | None,
                   reward_risk: float) -> tuple[float, float]:
    """(stop, target), rounded to the tick. Target defaults to `reward_risk`
    times the stop distance above the entry."""
    if entry <= 0:
        raise ManualError("no usable entry price")
    if stop <= 0 or stop >= entry:
        raise ManualError(f"stop {stop:,.2f} must be below the entry {entry:,.2f}")
    if target is None:
        if reward_risk <= 0:
            raise ManualError("give --target, or a positive --rr")
        target = entry + (entry - stop) * reward_risk
    if target <= entry:
        raise ManualError(f"target {target:,.2f} must be above the entry {entry:,.2f}")
    return round_tick(stop), round_tick(target)


def limit_price(last: float, offset_pct: float) -> float:
    """The scanner's measured cushion above the signal (+0.30%), so a limit
    at the last print does not miss a stock that ticks up on the send."""
    return round_tick(last * (1 + offset_pct / 100))


def sleeve_equity(equity: float, momentum_allocation_pct: float) -> float:
    """Equity the manual picks (and the scanner) are sized against: what the
    momentum sleeve does not claim."""
    share = max(0.0, min(100.0, 100.0 - momentum_allocation_pct))
    return equity * share / 100.0


def counted(held: Iterable[str], open_orders: Iterable[Mapping[str, Any]],
            owner_map: Mapping[str, str]) -> frozenset[str]:
    """Symbols that use a manual slot: held or ordered by anyone EXCEPT the
    momentum book, which has its own sleeve and its own ten names. Counting
    them would refuse every manual pick the day momentum fills."""
    from scanner.execution.ownership import MOMENTUM, owner, strategy_of
    names = {s for s in held if owner(s, owner_map) != MOMENTUM}
    names |= {str(o.get("symbol")) for o in open_orders
              if str(o.get("side")) == "buy"
              and strategy_of(o.get("client_order_id")) != MOMENTUM}
    return frozenset(names)


def sector_counts(symbols: Iterable[str],
                  sector_of: Mapping[str, str]) -> Counter[str]:
    return Counter(sector_of.get(s, "") for s in symbols if sector_of.get(s))


def check_sector(sector: str, counts: Mapping[str, int], cap: int) -> Verdict:
    """Refuse the (cap+1)th open position in one sector. 0 disables; an
    unknown sector cannot be counted, so it is allowed and the script warns."""
    if cap <= 0 or not sector:
        return ALLOWED
    held = counts.get(sector, 0)
    if held >= cap:
        return Verdict(allowed=False,
                       reason=f"already {held} open in {sector} (cap {cap})")
    return ALLOWED


def client_order_id(symbol: str, at: datetime) -> str:
    """`man-` prefix: the ownership contract (execution/ownership.py)."""
    return f"{prefix_for(MANUAL)}{symbol}-{int(at.timestamp())}"


def bracket(symbol: str, shares: int, *, limit: float, stop: float,
            target: float, at: datetime) -> dict[str, Any]:
    """Same order shape as the scanner's: limit entry, GTC, stop and target
    attached (see alpaca.bracket_payload for why GTC)."""
    if shares < 1:
        raise ManualError("size came out at zero shares")
    if not stop < limit < target:
        raise ManualError(f"need stop < limit < target, got "
                          f"{stop:.2f} / {limit:.2f} / {target:.2f}")
    return {
        "symbol": symbol,
        "qty": str(shares),
        "side": "buy",
        "type": "limit",
        "limit_price": f"{round_tick(limit):.2f}",
        "time_in_force": "gtc",
        "order_class": "bracket",
        "take_profit": {"limit_price": f"{round_tick(target):.2f}"},
        "stop_loss": {"stop_price": f"{round_tick(stop):.2f}"},
        "client_order_id": client_order_id(symbol, at),
        "extended_hours": False,
    }
