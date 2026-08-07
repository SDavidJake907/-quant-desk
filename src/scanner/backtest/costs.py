"""What it costs to actually take the trade.

WHY THIS MATTERS MORE INTRADAY THAN IT LOOKS
Costs are charged against the PRICE; results are measured in R, which is a
fraction of the price. So the same 10 basis points of friction is a rounding
error on a swing trade risking 8% and a third of the edge on a day trade
risking 1%. The setups this repo finds risk about 1.0-1.7% of price, which
puts them squarely in the range where friction decides the outcome.

WHAT IS CHARGED
    spread      you buy at the ask and sell at the bid; you cross half the
                quoted spread on each side
    slippage    the fill is worse than the quote — more so on a stop, which
                becomes a market order precisely when everyone else is selling
    commission  per share, per side, if your broker charges it

WHAT IS NOT MODELLED, AND WHY THAT FLATTERS THE RESULT
  - Stops fill at the stop price here. In a gap they fill lower, sometimes far
    lower, and gaps cluster on exactly the bad days.
  - The spread is one number per run, not per symbol per minute. Real spreads
    widen at the open, at the close, and during the news that produces these
    setups in the first place.
  - No market impact. Fine at retail size, not at scale.

So a result that survives this model has cleared a floor, not a realistic bar.

THE NUMBER WORTH READING
`breakeven_bps` — how much round-trip friction the edge can absorb before it
is gone. It converts "is 9 bps realistic?" into a question you can answer by
looking at your own fills rather than by arguing about assumptions.
"""

from __future__ import annotations

from typing import Sequence

from scanner.models import Frozen

#: Full quoted spread in basis points. Liquid large caps sit near 1-5;
#: small caps run far wider, which is where this default understates.
DEFAULT_SPREAD_BPS = 5.0

#: Adverse fill beyond the quote, per side.
DEFAULT_SLIPPAGE_BPS = 2.0


class CostModel(Frozen):
    """Round-trip friction, in basis points of price plus per-share fees."""

    spread_bps: float = DEFAULT_SPREAD_BPS
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    commission_per_share: float = 0.0

    @property
    def per_side_bps(self) -> float:
        """Half the spread is crossed each way, plus slippage each way."""
        return self.spread_bps / 2.0 + self.slippage_bps

    @property
    def round_trip_bps(self) -> float:
        return self.per_side_bps * 2.0

    def buy_price(self, quoted: float) -> float:
        """What you actually pay."""
        return quoted * (1 + self.per_side_bps / 10_000.0) + self.commission_per_share

    def sell_price(self, quoted: float) -> float:
        """What you actually receive."""
        return quoted * (1 - self.per_side_bps / 10_000.0) - self.commission_per_share


#: Costs off. Useful as the baseline a result is compared against.
FREE = CostModel(spread_bps=0.0, slippage_bps=0.0)


def net_r_multiple(
    entry: float, exit_price: float, stop: float, costs: CostModel
) -> float:
    """Result in R after friction.

    Risk stays the intended risk — entry minus stop as planned — because that
    is the number a position was sized from. Charging costs to the numerator
    and leaving the denominator alone is what a real account experiences.
    """
    risk = entry - stop
    if risk <= 0:
        return 0.0
    return (costs.sell_price(exit_price) - costs.buy_price(entry)) / risk


def breakeven_bps(
    gross_expectancy_r: float, risk_pcts: Sequence[float]
) -> float:
    """Round-trip friction, in bps, that would erase an edge.

    Cost in R for one trade is `round_trip_bps / (100 * risk_pct)` — INVERSELY
    proportional to the stop distance. So the average cost across a set of
    trades depends on the mean of 1/risk, not on 1/mean-risk, and the two are
    not close: the harmonic mean of these setups' risk is under half their
    arithmetic mean, because a handful of very tight stops dominate.

    Using the arithmetic mean here overstated survivable friction by more than
    2x — it reported a 32.5bps breakeven for a set that was already losing
    money at 20bps. Jensen's inequality, quietly flattering a backtest.
    """
    usable = [r for r in risk_pcts if r > 0]
    if not usable or gross_expectancy_r <= 0:
        return 0.0
    mean_inverse_risk = sum(1.0 / r for r in usable) / len(usable)
    return 100.0 * gross_expectancy_r / mean_inverse_risk
