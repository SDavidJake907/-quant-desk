"""Resolving setups into outcomes: did the target come first, or the stop?

TRADE MANAGEMENT IS THE WHOLE RESULT
A pattern does not have a return. A pattern plus an entry, a stop, a target
and a time limit has a return, and changing any of the four changes the
answer. So all four are explicit here and none of them is optimised.

GAPS, AND WHY THEY ARE NOT SYMMETRIC
A stop is a market order the moment it triggers. If a bar OPENS below the stop,
the fill is the open — not the stop — and the loss is larger than the 1R the
position was sized for. A target is a resting limit, so a bar that opens above
it fills at the open too, which is better than planned.

The asymmetry is the point: gaps enlarge losses and only occasionally enlarge
wins, and they cluster on the days that matter. A backtest that fills every
stop exactly at the stop price is quietly assuming that never happens. This
strategy holds overnight — only 54% of setups resolve inside one session — so
it is exposed to precisely the event the old assumption excluded.

THE INTRABAR PROBLEM, AND WHY IT IS SETTLED AGAINST THE TRADE
Daily or 4-hour bars record a high and a low but not their order. When one bar
touches both the stop and the target, the data cannot say which came first.
This module always assumes the STOP. That is not pessimism for its own sake:
the opposite assumption turns every whipsaw into a winner and is the single
most common way an intraday backtest manufactures an edge. Where the truth is
unknowable, the honest choice is the one that cannot flatter the result.

WHAT THE RANDOM CONTROL IS FOR
Every trade here has a stop, a target and a time limit, and that machinery
produces returns on its own — a 1:2 stop/target on a rising stock wins often
regardless of why you entered. The control runs identical management from
random entry bars in the same series. The pattern only means something if it
beats that. This is the same test that showed the coiled screen had no edge.
"""

from __future__ import annotations

import random
import statistics

from scanner.backtest.costs import FREE, CostModel, breakeven_bps, net_r_multiple
from scanner.features.intraday import IntradayBars
from scanner.models import Frozen
from scanner.screens.smc import Setup

#: Bars a trade may stay open before it is closed at the market.
DEFAULT_MAX_BARS = 60

TARGET = "target"
STOP = "stop"
TIMEOUT = "timeout"
OPEN = "open"


class Outcome(Frozen):
    """One resolved trade, in units of risk."""

    symbol: str
    entry_index: int
    entry_price: float
    stop_price: float
    target_price: float
    exit_index: int
    exit_price: float
    reason: str
    #: Whether the exit bar opened through the level, so the fill was the
    #: open rather than the level itself.
    gapped: bool = False

    @property
    def risk(self) -> float:
        return self.entry_price - self.stop_price

    @property
    def r_multiple(self) -> float:
        """Profit in multiples of the risk taken — the only comparable unit
        across stocks priced at $9 and $900."""
        if self.risk <= 0:
            return 0.0
        return (self.exit_price - self.entry_price) / self.risk

    def net_r(self, costs: CostModel) -> float:
        """R after friction. See `scanner.backtest.costs`."""
        return net_r_multiple(self.entry_price, self.exit_price, self.stop_price, costs)

    @property
    def risk_pct(self) -> float:
        """Stop distance as a share of price — what decides how much friction
        the trade can absorb."""
        if self.entry_price <= 0:
            return 0.0
        return self.risk / self.entry_price * 100.0

    @property
    def won(self) -> bool:
        return self.r_multiple > 0

    @property
    def bars_held(self) -> int:
        return self.exit_index - self.entry_index


def resolve(
    bars: IntradayBars,
    *,
    symbol: str,
    entry_index: int,
    entry_price: float,
    stop_price: float,
    target_price: float,
    max_bars: int = DEFAULT_MAX_BARS,
    end_index: int | None = None,
    gap_fills: bool = True,
) -> Outcome | None:
    """Walk forward from the entry bar until stop, target or time limit.

    Returns None if the trade is still open at the end of the series —
    counting an unresolved trade as a result would be inventing one.

    `gap_fills=False` restores the old assumption that every stop fills
    exactly at the stop price. It exists to measure what that assumption was
    worth, not because it is ever the more accurate choice.
    """
    if stop_price >= entry_price or target_price <= entry_price:
        return None

    boundary = len(bars) - 1 if end_index is None else min(end_index - 1, len(bars) - 1)
    if boundary <= entry_index:
        return None
    last = min(entry_index + max_bars, boundary)
    for i in range(entry_index + 1, last + 1):
        open_price = bars.opens[i]

        # A bar that OPENS through a level never traded at that level first.
        # The stop became a market order at the open; the target filled at the
        # open because a resting limit takes any better price.
        if gap_fills and open_price <= stop_price:
            return Outcome(
                symbol=symbol, entry_index=entry_index, entry_price=entry_price,
                stop_price=stop_price, target_price=target_price,
                exit_index=i, exit_price=open_price, reason=STOP, gapped=True,
            )
        if gap_fills and open_price >= target_price:
            return Outcome(
                symbol=symbol, entry_index=entry_index, entry_price=entry_price,
                stop_price=stop_price, target_price=target_price,
                exit_index=i, exit_price=open_price, reason=TARGET, gapped=True,
            )

        hit_stop = bars.lows[i] <= stop_price
        hit_target = bars.highs[i] >= target_price
        if hit_stop:  # checked first: one bar touching both is a loss
            return Outcome(
                symbol=symbol, entry_index=entry_index, entry_price=entry_price,
                stop_price=stop_price, target_price=target_price,
                exit_index=i, exit_price=stop_price, reason=STOP,
            )
        if hit_target:
            return Outcome(
                symbol=symbol, entry_index=entry_index, entry_price=entry_price,
                stop_price=stop_price, target_price=target_price,
                exit_index=i, exit_price=target_price, reason=TARGET,
            )

    if entry_index + max_bars > boundary:
        return None  # ran out of history before the trade resolved
    return Outcome(
        symbol=symbol, entry_index=entry_index, entry_price=entry_price,
        stop_price=stop_price, target_price=target_price,
        exit_index=last, exit_price=bars.closes[last], reason=TIMEOUT,
    )


def resolve_setups(
    bars: IntradayBars,
    setups: tuple[Setup, ...],
    *,
    max_bars: int = DEFAULT_MAX_BARS,
    start_index: int = 0,
    end_index: int | None = None,
    gap_fills: bool = True,
) -> tuple[Outcome, ...]:
    resolved = [
        resolve(
            bars,
            symbol=setup.symbol,
            entry_index=setup.trigger_index,
            entry_price=setup.entry_price,
            stop_price=setup.stop_price,
            target_price=setup.target_price,
            max_bars=max_bars,
            end_index=end_index,
            gap_fills=gap_fills,
        )
        for setup in setups
        if start_index <= setup.trigger_index
        and (end_index is None or setup.trigger_index < end_index)
    ]
    return tuple(o for o in resolved if o is not None)


def random_control(
    bars: IntradayBars,
    setups: tuple[Setup, ...],
    rng: random.Random,
    *,
    max_bars: int = DEFAULT_MAX_BARS,
    draws: int = 1,
    start_index: int = 0,
    end_index: int | None = None,
) -> tuple[Outcome, ...]:
    """Same trade management, same stop and target distances, random entries.

    Distances are copied as PERCENTAGES from the real setups, so the control
    is not a different strategy — it is this strategy with the pattern
    removed and only the timing randomised. Anything the control earns came
    from stops, targets and drift rather than from structure.
    """
    outcomes: list[Outcome] = []
    boundary = len(bars) if end_index is None else min(end_index, len(bars))
    first = max(1, start_index)
    last = boundary - 1
    if last <= first or boundary - first <= max_bars:
        return ()
    for setup in setups:
        stop_pct = (setup.entry_price - setup.stop_price) / setup.entry_price
        target_pct = (setup.target_price - setup.entry_price) / setup.entry_price
        for _ in range(draws):
            i = rng.randrange(first, last)
            entry = bars.closes[i]
            outcome = resolve(
                bars,
                symbol=setup.symbol,
                entry_index=i,
                entry_price=entry,
                stop_price=entry * (1 - stop_pct),
                target_price=entry * (1 + target_pct),
                max_bars=max_bars,
                end_index=boundary,
            )
            if outcome is not None:
                outcomes.append(outcome)
    return tuple(outcomes)


class Report(Frozen):
    """Aggregate performance of a set of trades."""

    label: str
    outcomes: tuple[Outcome, ...]

    @property
    def count(self) -> int:
        return len(self.outcomes)

    @property
    def win_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if o.won) / len(self.outcomes) * 100.0

    @property
    def expectancy(self) -> float:
        """Mean R per trade. Below zero the pattern costs money to trade."""
        if not self.outcomes:
            return 0.0
        return statistics.fmean(o.r_multiple for o in self.outcomes)

    @property
    def median_r(self) -> float:
        if not self.outcomes:
            return 0.0
        return statistics.median(o.r_multiple for o in self.outcomes)

    @property
    def total_r(self) -> float:
        return sum(o.r_multiple for o in self.outcomes)

    @property
    def stdev_r(self) -> float:
        if len(self.outcomes) < 2:
            return 0.0
        return statistics.stdev(o.r_multiple for o in self.outcomes)

    @property
    def t_stat(self) -> float:
        """Naive t on per-trade R. Treats trades as independent, which
        overlapping setups on one symbol are not — read it as an upper bound
        on confidence, never as a p-value."""
        if len(self.outcomes) < 2 or self.stdev_r == 0:
            return 0.0
        return self.expectancy / (self.stdev_r / (len(self.outcomes) ** 0.5))

    @property
    def mean_risk_pct(self) -> float:
        if not self.outcomes:
            return 0.0
        return statistics.fmean(o.risk_pct for o in self.outcomes)

    def net_expectancy(self, costs: CostModel = FREE) -> float:
        """Mean R after friction."""
        if not self.outcomes:
            return 0.0
        return statistics.fmean(o.net_r(costs) for o in self.outcomes)

    @property
    def harmonic_risk_pct(self) -> float:
        """Harmonic mean of stop distances — the average that matters when
        cost scales with 1/risk. Always at or below the arithmetic mean."""
        usable = [o.risk_pct for o in self.outcomes if o.risk_pct > 0]
        if not usable:
            return 0.0
        return len(usable) / sum(1.0 / r for r in usable)

    @property
    def breakeven_bps(self) -> float:
        """Round-trip friction this edge could absorb before vanishing."""
        return breakeven_bps(self.expectancy, [o.risk_pct for o in self.outcomes])

    @property
    def gapped(self) -> int:
        """Exits that filled through the level rather than at it."""
        return sum(1 for o in self.outcomes if o.gapped)

    @property
    def gap_cost_r(self) -> float:
        """R lost to stop fills worse than the stop, summed.

        Stops only. A gapped target is a windfall, and netting the two would
        hide how much the losing tail actually widened — which is the whole
        thing this measurement exists to expose.
        """
        total = 0.0
        for outcome in self.outcomes:
            if outcome.gapped and outcome.reason == STOP and outcome.risk > 0:
                total += (outcome.exit_price - outcome.stop_price) / outcome.risk
        return total

    def share(self, reason: str) -> float:
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if o.reason == reason) / len(self.outcomes) * 100.0
