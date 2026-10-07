"""The operator's list, traded automatically (WORKLIST §41).

    watchlists/picks.txt  ->  scripts/picks_sync.py, every 30 minutes in market hours

THE LIST IS THE DECISION
A name added to the list is bought at the next run; a name removed is sold.
Nothing else enters or exits except the safety stop. The operator chooses
WHAT to own; the system handles when the order goes, its size, and its
protection. Positions carry a `pck-` client id so every other rule knows
whose they are (execution/ownership.py).

THE EXITS
- Removed from the list: sold at market on the next run.
- The safety stop: `picks_stop_atr_multiple` x the stock's daily ATR below
  the entry. CHOSEN, NOT MEASURED -- wide enough that ordinary daily noise
  does not trip it, there to stop a collapse.
- No profit target. Alpaca's bracket needs one, so it sits at
  `picks_target_ceiling` x the entry, a price these names will not reach in a
  normal hold; it exists for the order shape and so protect.py can re-arm.

NEVER SHARES A POSITION. A listed name held or ordered by another strategy
(momentum, a one-off manual trade) is skipped and reported, not bought on
top. A position the list no longer names is sold only if picks owns it.

PURE. The script does the broker calls.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from scanner.execution.alpaca import round_tick
from scanner.execution.ownership import PICKS, UNKNOWN, owner, prefix_for, strategy_of
from scanner.models import Frozen

LIST_PATH = Path("watchlists/picks.txt")
_TICKER = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


class Plan(Frozen):
    buys: tuple[str, ...] = ()
    sells: tuple[str, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()


def read_list(path: Path = LIST_PATH) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(tickers, rejected). One per line, '#' comments, duplicates dropped.
    Anything that is not a plain US stock ticker is rejected, not guessed."""
    if not path.exists():
        return (), ()
    names: list[str] = []
    rejected: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.split("#", 1)[0].strip().upper()
        if not raw:
            continue
        if not _TICKER.match(raw):
            rejected.append(raw)
        elif raw not in names:
            names.append(raw)
    return tuple(names), tuple(rejected)


def plan(listed: Iterable[str], owner_map: Mapping[str, str],
         held: Iterable[str], pending_entries: Iterable[str]) -> Plan:
    """What to buy and sell to make the picks book match the list."""
    listed = tuple(listed)
    held, pending = set(held), set(pending_entries)
    buys, skipped = [], []
    for symbol in listed:
        who = owner(symbol, owner_map) if symbol in held else None
        if who == PICKS or symbol in pending:
            continue
        if who is not None:
            skipped.append((symbol, f"held by {who if who != UNKNOWN else 'hand (dashboard)'}"))
            continue
        buys.append(symbol)
    sells = tuple(sorted(s for s in held
                         if owner(s, owner_map) == PICKS and s not in listed))
    return Plan(buys=tuple(buys), sells=sells, skipped=tuple(skipped))


def levels(*, last: float, atr_pct: float, atr_multiple: float, offset_pct: float,
           ceiling: float) -> tuple[float, float, float]:
    """(limit, stop, target). Limit carries the scanner's measured +0.30%
    cushion; the stop sits `atr_multiple` daily ATRs under the limit."""
    if last <= 0 or atr_pct <= 0:
        raise ValueError("need a positive price and ATR to place a stop")
    limit = round_tick(last * (1 + offset_pct / 100))
    stop = round_tick(limit * (1 - atr_multiple * atr_pct / 100))
    if stop <= 0:
        raise ValueError(f"stop {stop} would be at or below zero")
    return limit, stop, round_tick(limit * ceiling)


def client_order_id(symbol: str, at: datetime) -> str:
    return f"{prefix_for(PICKS)}{symbol}-{int(at.timestamp())}"


def stale_entries(open_orders: Iterable[Mapping[str, Any]], now: datetime, *,
                  minutes: float) -> list[Mapping[str, Any]]:
    """Unfilled picks buys older than `minutes`: the price ran past the limit.
    Cancelled so the next run re-sends at the current price."""
    out = []
    for o in open_orders:
        if strategy_of(o.get("client_order_id")) != PICKS or o.get("side") != "buy":
            continue
        if float(o.get("filled_qty") or 0) > 0:
            continue
        try:
            sent = datetime.fromisoformat(str(o.get("submitted_at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if (now - sent).total_seconds() / 60 > minutes:
            out.append(o)
    return out
