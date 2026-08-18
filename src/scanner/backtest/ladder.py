"""Scaling out: what a take-profit ladder actually does to a distribution.

WHAT A LADDER CAN AND CANNOT DO
It cannot improve the left tail. A trade that runs straight to the stop never
reaches the first rung and loses a full -1R whether or not a ladder exists.
Every rung sits ABOVE the entry, so the only trades a ladder touches are the
ones already going your way. What it does is convert part of the right tail
into certainty.

That makes it a trap for anyone reading summary statistics. A ladder reliably
raises the win rate and reliably lowers the variance, and both of those look
like improvements in every report. On a strategy whose expectancy lives in a
few large winners, it can do all that while earning strictly less money. This
project has measured that exact outcome once already: an exit rule that fired
on winners took +1.25R trades down to +0.79R while lifting the win rate from
59% to 82%.

So the number to read is mean R, and the column to watch is the best trade.

WHY THE RUNNER MAY HAVE NO TARGET
`target_price=None` lets the final slice run to the time limit. For a momentum
strategy this is the point of the exercise: a fixed target caps the fat right
tail that momentum depends on, and capping it is the most likely reason a
momentum backtest underperforms a random entry with the same management.

COSTS ARE PER LEG, AND SCALE WITH THE STOP
Each exit crosses the spread. Expressed in R, that cost is friction divided by
stop distance — so the same spread is three times more damaging on a 1.5% stop
than on a 5% one. Passing a single hardcoded constant across strategies with
different stop widths silently mis-prices every one of them but the original.
"""

from __future__ import annotations

from typing import NamedTuple, Protocol, Sequence

#: A rung: (R multiple at which to sell, fraction of the ORIGINAL position).
Rung = tuple[float, float]


class LadderOutcome(NamedTuple):
    """What a managed trade earned, and how long it held the capital.

    `bars_held` is the reason this type exists. R per trade is the right
    measure only when capital is unlimited. Under a position limit, a slot is
    the scarce resource: a trade earning +0.15R over 6 sessions turns that slot
    over ten times while a +0.43R runner holds it for 60. Comparing the two on
    R alone answers a question nobody is trading.
    """

    r_multiple: float
    entry_index: int
    exit_index: int
    legs: int

    @property
    def bars_held(self) -> int:
        """Sessions the position occupied a slot, entry bar excluded."""
        return self.exit_index - self.entry_index


class OHLC(Protocol):
    """The bar geometry this module needs. Daily `Bars` and `IntradayBars`
    both satisfy it, so one resolver serves both timeframes."""

    opens: tuple[float, ...]
    highs: tuple[float, ...]
    lows: tuple[float, ...]
    closes: tuple[float, ...]

    def __len__(self) -> int: ...


def ladder_outcome(
    bars: OHLC,
    *,
    entry_index: int,
    entry_price: float,
    stop_price: float,
    target_price: float | None,
    rungs: Sequence[Rung] | None,
    max_bars: int,
    leg_cost_r: float = 0.0,
    gap_fills: bool = True,
    breakeven_after_first_rung: bool = False,
) -> LadderOutcome | None:
    """R-multiple of one trade managed with a take-profit ladder.

    `rungs=None` means no ladder: hold the whole position to the target, the
    stop or the time limit. That is the baseline every ladder must beat.

    `breakeven_after_first_rung` raises the stop to the entry price once the
    first rung has filled — the rule that makes a trade "free". It is not free.
    It buys a floor by taking on shake-out risk at exactly zero, and on a
    strategy that lives in its right tail, being flattened at breakeven during
    a normal pullback costs more than the floor is worth. Measure it, do not
    assume it.

    Returns None when the trade cannot be scored — no risk, or no history
    after the entry. Never returns a number it had to invent.
    """
    risk = entry_price - stop_price
    if risk <= 0:
        return None

    ladder = tuple(rungs or ())
    if ladder and sum(fraction for _, fraction in ladder) > 1.0 + 1e-9:
        raise ValueError("ladder fractions sum to more than the whole position")

    end = min(len(bars), entry_index + 1 + max_bars)
    if end <= entry_index + 1:
        return None

    remaining, realised = 1.0, 0.0
    filled = [False] * len(ladder)

    def _exit(price: float, index: int) -> LadderOutcome:
        """Close what is left at `price` and charge one leg for doing it."""
        r = realised + remaining * ((price - entry_price) / risk - leg_cost_r)
        return LadderOutcome(r, entry_index, index, sum(filled) + 1)

    #: Raised to the entry once a rung fills, and only from the FOLLOWING bar.
    #: A bar that reaches the rung and also trades back through the entry does
    #: not record which came first; moving the stop inside that bar would be
    #: reading an order the data does not contain.
    stop = stop_price

    for i in range(entry_index + 1, end):
        open_, high, low = bars.opens[i], bars.highs[i], bars.lows[i]

        # A bar that OPENS through the stop never traded at the stop first: the
        # order became a market order at the open and the loss is larger than
        # the position was sized for.
        if gap_fills and open_ <= stop:
            return _exit(open_, i)
        if low <= stop:
            # Checked BEFORE any rung. A bar recording both a new high and the
            # stop does not say which came first, and the honest reading is the
            # one that cannot flatter the result.
            return _exit(stop, i)

        for n, (level, fraction) in enumerate(ladder):
            if filled[n] or remaining <= 0.0:
                continue
            if high >= entry_price + level * risk:
                take = min(fraction, remaining)
                realised += take * (level - leg_cost_r)
                remaining -= take
                filled[n] = True
                if breakeven_after_first_rung:
                    stop = max(stop, entry_price)

        if remaining <= 1e-9:
            return LadderOutcome(realised, entry_index, i, sum(filled))
        if target_price is not None and high >= target_price:
            return _exit(target_price, i)

    return _exit(bars.closes[end - 1], end - 1)


def ladder_r(bars: OHLC, **kwargs) -> float | None:
    """The R-multiple alone, for callers that do not care how long it took.

    Kept because most reporting only needs the number — but note that any
    comparison between variants with different hold times needs `bars_held`
    from `ladder_outcome`, not this.
    """
    outcome = ladder_outcome(bars, **kwargs)
    return None if outcome is None else outcome.r_multiple
