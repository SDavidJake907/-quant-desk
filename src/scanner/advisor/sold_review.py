"""Every position sold in the account lately, for Gemma to learn from (operator 2026-10-23:
"lets send all the sold positions to Gemma to view").

Her short memory (gemma_memory.py) shows only her own last six closed trades. This
shows every round trip in the last DAYS days across EVERY book (hers, the operator's
picks, one-off trades), who sold it and why, and -- the part a trade record cannot
show -- what the stock did after the sale. A sale followed by a rally may have been
too early; one followed by a slide was a good sale. Information, never an instruction.

Round trips are built FIFO from the broker's fills; the opening buy's client_order_id
names the book (execution/ownership.py). PURE: fills, orders, reasons and prices in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta

from scanner.execution import ownership
from scanner.models import Frozen

DAYS = 30
MAX_LINES = 25
#: A move this big after a sale is called out (sold early / good sale).
NOTABLE_PCT = 5.0
BOOK = {ownership.GEMMA: "yours", ownership.PICKS: "operator's picks", ownership.MANUAL: "operator's one-off",
        ownership.ORB: "breakout book", ownership.MOMENTUM: "momentum", ownership.SMC: "scanner"}


class Trip(Frozen):
    symbol: str
    book: str
    qty: float
    entry: float
    exit: float
    opened: datetime
    closed: datetime
    why: str = "sold"

    @property
    def dollars(self) -> float:
        return (self.exit - self.entry) * self.qty

    @property
    def pct(self) -> float:
        return (self.exit / self.entry - 1) * 100 if self.entry else 0.0


def _when(text: str) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def by_order(fills: Iterable[Mapping]) -> list[dict]:
    """One fill per order: an order filled in pieces is one buy or one sale (summed
    shares, share-weighted price, first fill's time)."""
    merged: dict[str, dict] = {}
    for f in sorted(fills, key=lambda f: str(f.get("transaction_time"))):
        key = str(f.get("order_id") or f.get("id"))
        qty, px = float(f["qty"]), float(f["price"])
        if key not in merged:
            merged[key] = {**f, "qty": qty, "price": px}
            continue
        m = merged[key]
        total = m["qty"] + qty
        merged[key] = {**m, "qty": total, "price": (m["price"] * m["qty"] + px * qty) / total}
    return list(merged.values())


def round_trips(fills: Iterable[Mapping], orders: Mapping[str, Mapping]) -> list[Trip]:
    """Closed round trips, oldest first. `orders` maps order id -> order (for the book
    of the opening buy and the type of the closing sell). Shares sold that were bought
    before the fills start (no lot to match) are skipped, not guessed."""
    lots: dict[str, list[list]] = {}      # symbol -> [qty, price, time, book]
    open_trip: dict[tuple, dict] = {}     # (symbol, buy time) -> accumulating sale
    out: list[Trip] = []
    for f in sorted(by_order(fills), key=lambda f: str(f.get("transaction_time"))):
        s, qty, px = str(f["symbol"]), float(f["qty"]), float(f["price"])
        at = _when(f["transaction_time"])
        order = orders.get(str(f.get("order_id")), {})
        if f.get("side") == "buy":
            book = ownership.strategy_of(order.get("client_order_id"))
            lots.setdefault(s, []).append([qty, px, at, book])
            continue
        kind = "stop hit" if str(order.get("type")) in ("stop", "stop_limit", "trailing_stop") else "sold"
        while qty > 1e-9 and lots.get(s):
            lot = lots[s][0]
            take = min(qty, lot[0])
            key = (s, lot[2])
            acc = open_trip.setdefault(key, {"qty": 0.0, "cash": 0.0, "cost": lot[1], "book": lot[3],
                                            "opened": lot[2], "kind": kind})
            acc["qty"] += take
            acc["cash"] += take * px
            acc["closed"], acc["kind"] = at, kind
            lot[0] -= take
            qty -= take
            if lot[0] <= 1e-9:
                lots[s].pop(0)
                done = open_trip.pop(key)
                out.append(Trip(symbol=s, book=done["book"], qty=done["qty"], entry=done["cost"],
                                exit=done["cash"] / done["qty"], opened=done["opened"],
                                closed=done["closed"], why=done["kind"]))
    return out


def with_reasons(trips: Sequence[Trip], exits: Iterable[tuple[str, datetime, str]],
                 window: timedelta = timedelta(minutes=15)) -> list[Trip]:
    """Attach the logged reason (audit '*_exit' events: symbol, time, reason) closest
    before each sale, within `window`. A broker stop fill keeps 'stop hit'."""
    by_symbol: dict[str, list[tuple[datetime, str]]] = {}
    for s, at, why in exits:
        by_symbol.setdefault(s, []).append((at, why))
    out = []
    for t in trips:
        near = [(abs((t.closed - at).total_seconds()), why) for at, why in by_symbol.get(t.symbol, ())
                if abs(t.closed - at) <= window]
        why = min(near)[1].split("; ")[0][:110] if near else ""
        out.append(t.model_copy(update={"why": why}) if why and t.why != "stop hit" else t)
    return out


def _held_for(t: Trip) -> str:
    span = t.closed - t.opened
    if span < timedelta(hours=1):
        return f"{max(1, int(span.total_seconds() // 60))} min"
    if span < timedelta(days=1):
        return f"{span.total_seconds() / 3600:.0f} h"
    return f"{span.days} d"


def block(trips: Sequence[Trip], now_price: Mapping[str, float], *, days: int = DAYS) -> str:
    """The prompt block: newest sale first, each with the move since the sale."""
    if not trips:
        return ""
    newest = sorted(trips, key=lambda t: t.closed, reverse=True)[:MAX_LINES]
    lines = [f"EVERY POSITION SOLD IN THE LAST {days} DAYS, all books (what happened after each sale -- "
             "learn from it; it is never by itself a reason to buy or sell):"]
    early = good = 0
    for t in newest:
        now = now_price.get(t.symbol)
        since = (now / t.exit - 1) * 100 if now and t.exit else None
        if since is not None and since >= NOTABLE_PCT:
            early += 1
        elif since is not None and since <= -NOTABLE_PCT:
            good += 1
        after = "" if since is None else f"; now {now:,.2f} ({since:+.1f}% since the sale)"
        lines.append(f"- {t.symbol} ({BOOK.get(t.book, 'unattributed')}) {t.closed:%m-%d}: bought "
                     f"{t.entry:,.2f}, sold {t.exit:,.2f} after {_held_for(t)}, ${t.dollars:+,.0f} "
                     f"({t.pct:+.1f}%) -- {t.why}{after}")
    won = sum(t.dollars > 0 for t in trips)
    shown = f" (newest {len(newest)} listed)" if len(newest) < len(trips) else ""
    lines.append(f"Totals: {len(trips)} sold{shown}, {won} at a gain, ${sum(t.dollars for t in trips):+,.0f}; "
                 f"{early} rose {NOTABLE_PCT:g}%+ after the sale (sold early?), {good} fell "
                 f"{NOTABLE_PCT:g}%+ after it (good sales).")
    return "\n".join(lines)
