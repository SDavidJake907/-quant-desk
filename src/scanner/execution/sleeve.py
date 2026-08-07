"""Two pots of money in one brokerage account, kept apart.

WHY THIS EXISTS
`size_position` risks a percentage of "equity", and every backtest number in
this repo assumes that equity is the capital the strategy actually has. Park a
third of the account in dividend holdings that are never sold, and that
assumption silently breaks: the swing strategy keeps sizing off the full
balance while trading out of two thirds of it. Risk per trade rises by half,
the position cap rises by half, and nothing reports an error. The account just
becomes more concentrated than anyone chose.

So: equity is split before it reaches sizing, and each sleeve sees only its
own money.

    income   held continuously; collects dividends; never sized by this code
    swing    the SMC strategy; sized against `swing` equity alone

HOW A POSITION KNOWS WHICH SLEEVE IT IS IN
It doesn't — the broker has no such field. Membership is inferred from the
client order id, which this system already stamps with a strategy prefix. A
position bought by the scanner carries `smc-`; anything else was bought by a
human or by the income sleeve and is not the swing strategy's business.

That is the same mechanism the reconciler uses to keep a manual purchase out
of the strategy's statistics, and it is deliberate reuse: one convention,
applied in one place, is far harder to get wrong than two.

WHAT THIS DOES NOT DO
It does not choose the allocation, rebalance, or place income orders. It draws
the line and reports which side of it things fall on. Crossing that line is a
deliberate act, not a side effect of a position drifting in value.
"""

from __future__ import annotations

from scanner.models import Frozen

#: Order-id prefix stamped on every position the SMC scanner opens.
SWING_PREFIX = "smc-"

INCOME = "income"
SWING = "swing"


class Sleeves(Frozen):
    """Account equity divided into the pot each strategy may draw on."""

    total_equity: float
    swing_equity: float
    income_equity: float
    swing_allocation_pct: float

    #: Ceiling on the swing sleeve, if one is set. Profit above it becomes
    #: income capital without a transfer being made.
    swing_cap: float | None = None

    #: Value of open positions the swing strategy actually opened.
    swing_deployed: float = 0.0

    #: Value of everything else — income holdings and manual buys alike.
    untracked_deployed: float = 0.0

    @property
    def swing_available(self) -> float:
        """Swing capital not already in a position.

        Never negative: an over-deployed sleeve reports zero headroom rather
        than a negative number that would flow into sizing as a nonsense cap.
        """
        return max(0.0, self.swing_equity - self.swing_deployed)

    @property
    def swing_utilisation(self) -> float:
        """Share of the swing sleeve currently deployed, as a percentage."""
        if self.swing_equity <= 0:
            return 0.0
        return self.swing_deployed / self.swing_equity * 100.0

    @property
    def over_deployed(self) -> bool:
        """True when swing positions exceed the sleeve that is meant to fund
        them — the signal that the allocation was changed underneath open
        trades, rather than that anything is wrong with a trade."""
        return self.swing_deployed > self.swing_equity

    def describe(self) -> str:
        return (f"swing {self.swing_equity:,.0f} "
                f"({self.swing_allocation_pct:.0f}% of {self.total_equity:,.0f}), "
                f"{self.swing_deployed:,.0f} deployed, "
                f"{self.swing_available:,.0f} free")


def is_swing_position(client_order_id: str | None) -> bool:
    """Whether a position was opened by the SMC scanner.

    Absent or unrecognised ids are treated as NOT ours. That asymmetry is
    intentional: mistaking a manual holding for a strategy position would let
    the strategy size against, and potentially close, money it never committed.
    The reverse error only makes the strategy too cautious.
    """
    return bool(client_order_id) and client_order_id.startswith(SWING_PREFIX)


def split(
    total_equity: float,
    *,
    swing_allocation_pct: float = 100.0,
    swing_cap: float | None = None,
    positions: tuple[tuple[str | None, float], ...] = (),
) -> Sleeves:
    """Divide equity into sleeves and attribute open positions to each.

    `positions` is (client_order_id, market_value) per open position — the two
    fields needed to answer "is this ours" and "how much is committed".

    An allocation of 100 means every dollar is the swing strategy's, which is
    how this system behaved before sleeves existed. That is the default so
    that adding this module changes no behaviour until an allocation is
    deliberately set.

    PERCENTAGE OR CAP — THESE COMPOUND DIFFERENTLY
    A percentage floats: the sleeve grows with the account, so profit stays
    where it was made and both sleeves compound in step. A `swing_cap` holds
    the strategy at a fixed size and lets everything above it fall to income,
    which is how profit is moved from one sleeve to the other without a
    transfer ever being made. Neither is more correct — they express different
    intentions, and only the cap makes the income sleeve grow faster than the
    account. When both are given the cap binds, because a ceiling that a
    percentage could raise is not a ceiling.
    """
    pct = min(100.0, max(0.0, swing_allocation_pct))
    equity = max(0.0, total_equity)

    swing_equity = equity * pct / 100.0
    if swing_cap is not None:
        swing_equity = min(swing_equity, max(0.0, swing_cap))
    swing_deployed = sum(
        max(0.0, value) for order_id, value in positions
        if is_swing_position(order_id)
    )
    untracked = sum(
        max(0.0, value) for order_id, value in positions
        if not is_swing_position(order_id)
    )
    return Sleeves(
        total_equity=equity,
        swing_equity=swing_equity,
        income_equity=equity - swing_equity,
        swing_allocation_pct=pct,
        swing_cap=swing_cap,
        swing_deployed=swing_deployed,
        untracked_deployed=untracked,
    )
