"""Joining what we intended to what the broker actually did.

WHY THIS EXISTS
A paper run only becomes evidence if every trade can be traced from intent to
outcome. Without that, "it seems to be working" is the best anyone can say —
which is exactly how the crypto projects and the pre-holdout screens in this
repo looked right up until they were measured.

THE NUMBER NOBODY HAS YET
Fill rate. The backtest assumed entry AT the trigger bar's close; live, a limit
order rests there and fills only if price comes back. So the setups that ran
away immediately never fill — and if those are the winners, live results will
sit below the backtest for a reason no amount of re-testing would reveal.
`Summary.fill_rate` is the first honest measurement of that gap.

R IS COMPUTED AGAINST THE INTENDED RISK
Not the filled risk. Position size was chosen from the planned entry-to-stop
distance, so that is the denominator the backtest's R-multiples describe. A
fill a few cents away changes the numerator, as it does in a real account.
"""

from __future__ import annotations

import statistics
from typing import Any, Iterable

from scanner.models import Frozen

WORKING = "working"
FILLED_OPEN = "open"
CLOSED_TARGET = "target"
CLOSED_STOP = "stop"
CLOSED_OTHER = "closed"
#: Closed by an order that was not a leg of its own bracket — a replacement
#: bracket, a manual sale, or the Friday flatten's DELETE /v2/positions.
CLOSED_FLAT = "flattened"
EXPIRED = "expired"
REJECTED = "rejected"

#: Order statuses that mean the entry never happened.
_DEAD = {"canceled", "expired", "rejected", "done_for_day", "replaced"}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class LiveTrade(Frozen):
    """One intent, and whatever the broker made of it."""

    symbol: str
    client_order_id: str
    order_id: str = ""
    shares: int = 0
    intended_entry: float = 0.0
    intended_stop: float = 0.0
    intended_target: float = 0.0
    state: str = WORKING
    entry_fill: float | None = None
    exit_fill: float | None = None
    submitted_at: str = ""
    #: Whether this order came from this system. Positions opened by hand in
    #: the broker's app appear here too — they must be VISIBLE, since an
    #: untracked position is the one you most need to see, but they must never
    #: enter the statistics or the paper record stops measuring the strategy
    #: and starts measuring the operator.
    tracked: bool = True

    @property
    def intended_risk(self) -> float:
        return self.intended_entry - self.intended_stop

    @property
    def is_closed(self) -> bool:
        return self.state in {CLOSED_TARGET, CLOSED_STOP, CLOSED_OTHER,
                              CLOSED_FLAT}

    @property
    def filled(self) -> bool:
        return self.entry_fill is not None

    @property
    def slippage(self) -> float | None:
        """Fill minus intended entry. Positive means paying up."""
        if self.entry_fill is None or not self.tracked or self.intended_entry <= 0:
            return None
        return self.entry_fill - self.intended_entry

    @property
    def stop_fill_gap(self) -> float | None:
        """How far a stop filled from its level. Positive is in our favour.

        Worth watching. On 2026-08-06 an AMD stop set at 485.55 filled at
        488.56 in the paper account — three dollars BETTER than the level. A
        real stop becomes a market order the instant it triggers and fills at
        or worse than the level, never above it. If this stays positive, paper
        results are flattered against both the backtest and a funded account.
        """
        if self.state != CLOSED_STOP or self.exit_fill is None:
            return None
        return self.exit_fill - self.intended_stop

    def r_multiple(self, mark: float | None = None) -> float | None:
        """Result in R. `mark` prices an open position; closed trades ignore it.

        Denominated in the INTENDED risk, because that is what the position
        was sized from and what the backtest's numbers mean.
        """
        if self.entry_fill is None or self.intended_risk <= 0:
            return None
        exit_price = self.exit_fill if self.is_closed else mark
        if exit_price is None:
            return None
        return (exit_price - self.entry_fill) / self.intended_risk


def classify(order: dict) -> tuple[str, float | None, float | None]:
    """(state, entry fill, exit fill) for one bracket order and its legs."""
    status = str(order.get("status") or "").lower()
    entry_fill = _number(order.get("filled_avg_price"))

    if status == "rejected":
        return REJECTED, None, None
    if entry_fill is None:
        return (EXPIRED if status in _DEAD else WORKING), None, None

    for leg in order.get("legs") or []:
        leg_fill = _number(leg.get("filled_avg_price"))
        if leg_fill is None:
            continue
        leg_type = str(leg.get("type") or "").lower()
        if "stop" in leg_type:
            return CLOSED_STOP, entry_fill, leg_fill
        if "limit" in leg_type:
            return CLOSED_TARGET, entry_fill, leg_fill
        return CLOSED_OTHER, entry_fill, leg_fill

    return FILLED_OPEN, entry_fill, None


def reconcile(
    intents: dict[str, dict], orders: Iterable[dict]
) -> tuple[LiveTrade, ...]:
    """Join audit-log intents to broker orders on `client_order_id`.

    Orders the broker knows about but the log does not are still reported —
    an untracked position is the one you most need to see.
    """
    trades: list[LiveTrade] = []
    seen: set[str] = set()

    for order in orders:
        coid = str(order.get("client_order_id") or "")
        intent = intents.get(coid, {})
        seen.add(coid)
        state, entry_fill, exit_fill = classify(order)
        trades.append(LiveTrade(
            tracked=bool(intent),
            symbol=str(order.get("symbol") or intent.get("symbol") or "?"),
            client_order_id=coid,
            order_id=str(order.get("id") or ""),
            shares=int(_number(order.get("qty")) or intent.get("shares") or 0),
            intended_entry=float(intent.get("entry_price") or
                                 _number(order.get("limit_price")) or 0.0),
            intended_stop=float(intent.get("stop_price") or 0.0),
            intended_target=float(intent.get("target_price") or 0.0),
            state=state, entry_fill=entry_fill, exit_fill=exit_fill,
            submitted_at=str(order.get("submitted_at") or ""),
        ))

    # Intents with no matching order: submitted and lost, or never sent.
    for coid, intent in intents.items():
        if coid in seen:
            continue
        trades.append(LiveTrade(
            symbol=str(intent.get("symbol") or "?"),
            client_order_id=coid,
            shares=int(intent.get("shares") or 0),
            intended_entry=float(intent.get("entry_price") or 0.0),
            intended_stop=float(intent.get("stop_price") or 0.0),
            intended_target=float(intent.get("target_price") or 0.0),
            state=REJECTED,
        ))

    trades = _attach_unattached_closes(trades, orders)
    trades.sort(key=lambda t: t.submitted_at, reverse=True)
    return tuple(trades)


def _closing_kind(order: dict) -> tuple[str, float | None]:
    """How an unattached sell closed a position, and at what price.

    A replacement bracket carries its own legs, so the filled leg names the
    reason. A bare market or limit sale has none and is read from its own type.
    """
    for leg in order.get("legs") or []:
        fill = _number(leg.get("filled_avg_price"))
        if fill is None:
            continue
        leg_type = str(leg.get("type") or "").lower()
        if "stop" in leg_type:
            return CLOSED_STOP, fill
        if "limit" in leg_type:
            return CLOSED_TARGET, fill
    fill = _number(order.get("filled_avg_price"))
    order_type = str(order.get("type") or "").lower()
    if "stop" in order_type:
        return CLOSED_STOP, fill
    if "limit" in order_type:
        return CLOSED_TARGET, fill
    return CLOSED_FLAT, fill


def _attach_unattached_closes(trades: list[LiveTrade], orders) -> list[LiveTrade]:
    """Pair still-open trades with sells that were not their own bracket legs.

    THE BUG THIS FIXES
    `classify` looks only inside `order["legs"]`. A position closed by anything
    else — a replacement bracket, a manual sale, or flatten.py's
    DELETE /v2/positions — leaves the entry's own legs expired or cancelled, so
    the trade reads open forever. Four such trades were sitting in the live
    account while the broker held nothing.

    It matters more now than it did: the Friday flatten produces exactly this
    shape, so without it every flattened trade would be uncountable and the
    evaluation window would only ever see the trades that closed cleanly
    through their bracket — the biased subset.

    Matching is by symbol and chronology, and each closing order is consumed
    once so two entries in one name cannot both claim the same sale. Trades
    with no candidate are left open, because a genuinely open position must not
    be closed by bookkeeping.
    """
    def _has_fill(order: dict) -> bool:
        """A replacement bracket's PARENT is often cancelled while one of its
        legs fills — that is what a stop doing its job looks like. Requiring a
        parent fill misses exactly those, which is how IP stayed open against
        a broker holding nothing."""
        if _number(order.get("filled_avg_price")) is not None:
            return True
        return any(_number(leg.get("filled_avg_price")) is not None
                   for leg in order.get("legs") or [])

    sells = sorted(
        (o for o in orders
         if str(o.get("side") or "").startswith("sell") and _has_fill(o)),
        key=lambda o: str(o.get("filled_at") or o.get("submitted_at") or ""),
    )
    used: set[int] = set()
    resolved: list[LiveTrade] = []
    for trade in sorted(trades, key=lambda t: t.submitted_at):
        if trade.state != FILLED_OPEN or not trade.tracked:
            resolved.append(trade)
            continue
        for index, sell in enumerate(sells):
            if index in used:
                continue
            if str(sell.get("symbol") or "") != trade.symbol:
                continue
            when = str(sell.get("filled_at") or sell.get("submitted_at") or "")
            if when <= trade.submitted_at:
                continue          # sold before this entry: closed something else
            state, fill = _closing_kind(sell)
            used.add(index)
            trade = trade.model_copy(update={"state": state, "exit_fill": fill})
            break
        resolved.append(trade)
    return resolved


class Summary(Frozen):
    """What the run has produced so far."""

    trades: tuple[LiveTrade, ...]

    @property
    def ours(self):
        """Only trades this system placed. Every statistic counts these."""
        return tuple(t for t in self.trades if t.tracked)

    @property
    def untracked(self):
        """Positions opened outside the system — shown, never scored."""
        return tuple(t for t in self.trades if not t.tracked)

    @property
    def sent(self) -> int:
        return sum(1 for t in self.ours if t.state != REJECTED)

    @property
    def filled(self) -> int:
        return sum(1 for t in self.ours if t.filled)

    @property
    def fill_rate(self) -> float:
        """The measurement the backtest could not make."""
        return (self.filled / self.sent * 100.0) if self.sent else 0.0

    @property
    def working(self) -> int:
        return sum(1 for t in self.ours if t.state == WORKING)

    @property
    def open_positions(self) -> int:
        return sum(1 for t in self.ours if t.state == FILLED_OPEN)

    @property
    def closed(self) -> tuple[LiveTrade, ...]:
        return tuple(t for t in self.ours if t.is_closed)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.closed if (t.r_multiple() or 0) > 0)

    @property
    def realized_r(self) -> float:
        return sum(t.r_multiple() or 0.0 for t in self.closed)

    @property
    def expectancy(self) -> float:
        values = [t.r_multiple() for t in self.closed if t.r_multiple() is not None]
        return statistics.fmean(values) if values else 0.0

    @property
    def mean_stop_fill_gap(self) -> float:
        """Average distance stops filled from their level, in dollars.

        Should be zero or negative in a funded account. Persistently
        positive means the venue is filling stops better than a market
        order ever would, and the record is flattered.
        """
        values = [t.stop_fill_gap for t in self.ours if t.stop_fill_gap is not None]
        return statistics.fmean(values) if values else 0.0

    @property
    def mean_slippage(self) -> float:
        values = [t.slippage for t in self.ours if t.slippage is not None]
        return statistics.fmean(values) if values else 0.0
