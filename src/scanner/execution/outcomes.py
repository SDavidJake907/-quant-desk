"""Writing back what actually happened to a trade.

THE GAP THIS CLOSES
The audit log records what was DECIDED and never what came of it. Realized R is
recomputed on demand from Alpaca's order history, which is not permanent — the
broker prunes it, and when it goes the only record of how a trade ended goes
with it.

Eight dimensions are logged on every decision and enforced on none, each so it
could be settled later on data nobody chose it from: the RSI gate, session
slot, weekday, LLM ranking, the equal-lows pool, liquidity tier, priority rank,
and the uncapped-exit candidate. Every one of them is unanswerable until the
outcome sits next to the decision in the same permanent file.

APPEND ONLY
An outcome is a NEW record linked by `client_order_id`, which is permanent and
already on every intent. Earlier records are never rewritten. That is the whole
value of an append-only log: an outcome written by editing the original intent
would be indistinguishable from an intent that had always said so, and a log
that can be revised after the fact cannot settle anything.

MEASURED FROM FILLS, NOT FROM INTENT
Realized R uses the prices the broker actually gave. A stop that filled a third
of an R better than its level did not lose 1.00R, and recording the intended
outcome would quietly launder execution quality out of the evidence — the
opposite of what this file is for.
"""

from __future__ import annotations

from datetime import datetime

from scanner.models import TradeOutcome

#: Exit reasons, mirroring reconcile's states. `flattened` is kept distinct so
#: the weekend rule's cost can be read straight off the log.
STOP, TARGET, FLATTENED, OTHER = "stop", "target", "flattened", "closed"

EVENT = "trade_outcome"


def outcome_from(trade) -> TradeOutcome | None:
    """Build an outcome from a reconciled trade, or None if it has none yet.

    Returns None rather than a zero for anything unresolved, untracked, or
    lacking a stop distance. A zero would average into the expectancy as though
    it were a measurement.
    """
    if not getattr(trade, "tracked", False) or not trade.is_closed:
        return None
    if trade.exit_fill is None or trade.entry_fill is None:
        return None
    risk = trade.intended_entry - trade.intended_stop
    if risk <= 0:
        return None
    return TradeOutcome(
        client_order_id=trade.client_order_id,
        symbol=trade.symbol,
        exit_reason=trade.state,
        entry_fill=float(trade.entry_fill),
        exit_fill=float(trade.exit_fill),
        shares=int(trade.shares or 0),
        # From the FILLS. What was intended is already in the intent record.
        realized_r=(trade.exit_fill - trade.entry_fill) / risk,
        realized_dollars=(trade.exit_fill - trade.entry_fill) * int(trade.shares or 0),
        intended_entry=float(trade.intended_entry),
        intended_stop=float(trade.intended_stop),
        intended_target=float(trade.intended_target),
    )


def already_recorded(client_order_id: str, seen: set[str]) -> bool:
    """Whether this trade already has an outcome in the log.

    The job runs on a schedule and again after every flatten, so writing twice
    is the normal case to defend against — a duplicated outcome double-counts
    the trade in every statistic drawn from the log.
    """
    return client_order_id in seen
