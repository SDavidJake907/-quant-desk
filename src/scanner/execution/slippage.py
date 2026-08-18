"""What fills actually cost, in units that can be compared with expectancy.

THE REPORTING BUG THIS EXISTS TO FIX
`positions.py` reported "mean stop fill +1.4955 vs the stop level" — dollars
per share, averaged across trades with wildly different stop distances. That
number was quoted repeatedly as evidence paper fills were optimistic, and it
could not support the claim. AMD's stop sat $8.51 below its entry and CCL's
$0.61 below; averaging a dollar gap across those two says nothing.

Converted properly: AMD filled +0.354R above its stop, CCL -0.033R below.
Only in R can fills be averaged, and only in R can a fill be set against the
+0.218R expectancy it erodes.

SIGN CONVENTION: POSITIVE IS ALWAYS A COST
A buy filling high and a sell filling low are both adverse and have opposite
arithmetic. Folding the side into the sign makes a mean over mixed records
meaningful. The reviewer's formula, generalised:

    adverse_r = (actual - intended) / risk      for a buy
    adverse_r = (intended - actual) / risk      for a sell

WHY FAVOURABLE FILLS ARE RECORDED RATHER THAN DISCARDED
A real stop becomes a market order the instant it triggers and fills at or
worse than its level, never above it. Paper fills that come in better are not
good luck — they are the simulator being generous, and a run where they
dominate is flattered against both the backtest and a funded account. Counting
them is how that shows up.

WHAT THIS DELIBERATELY DOES NOT DO
It does not estimate a distribution. With two stopped trades, grouping by
gap-versus-normal, liquidity, price, ATR, time of day and win-versus-loss
produces cells of size one — eight ways of relabelling a single trade. This
records and reports honestly, marks small samples as unreportable, and leaves
modelling until the frozen window has produced trades to model.
"""

from __future__ import annotations

import statistics

from scanner.models import Frozen

#: Records needed before a mean is worth quoting. Not a significance threshold
#: — a floor below which a "distribution" is a handful of anecdotes.
MIN_REPORTABLE = 20

BUY = "buy"
SELL = "sell"


class FillRecord(Frozen):
    """One fill, against the price that was intended for it."""

    symbol: str
    #: entry | stop | target | flatten — the flatten bucket exists because a
    #: forced Friday exit is a market order into the close and has no reason to
    #: behave like a stop or a target.
    kind: str
    side: str
    intended: float
    actual: float
    risk_per_share: float
    #: True when the bar opened through the level, so the fill was never going
    #: to be at it. Left unset when unknown rather than guessed.
    gapped: bool | None = None

    @property
    def adverse_r(self) -> float | None:
        """Cost of this fill in R. Positive is money lost to execution.

        None when there is no stop distance to divide by — reporting zero
        there would read as a perfect fill.
        """
        if self.risk_per_share <= 0:
            return None
        raw = (self.actual - self.intended if self.side == BUY
               else self.intended - self.actual)
        return raw / self.risk_per_share

    @property
    def is_adverse(self) -> bool:
        value = self.adverse_r
        return value is not None and value > 0

    @property
    def is_favourable(self) -> bool:
        """A fill better than intended. On a stop this is impossible live."""
        value = self.adverse_r
        return value is not None and value < 0


class SlippageSummary(Frozen):
    """Aggregate fill cost, with the tail kept separate from the mean."""

    count: int
    mean_adverse_r: float | None
    median_adverse_r: float | None
    worst_adverse_r: float | None
    favourable: int

    @property
    def is_reportable(self) -> bool:
        """Whether the sample is large enough to quote a mean from."""
        return self.count >= MIN_REPORTABLE

    @property
    def favourable_share(self) -> float:
        return self.favourable / self.count * 100.0 if self.count else 0.0


def summarise(records) -> SlippageSummary:
    """Aggregate a set of fills.

    The worst case is reported alongside the mean on purpose. Subtracting an
    average understates precisely the fills that hurt, and a replay that wants
    to be honest samples from the distribution rather than shifting by its
    centre.
    """
    values = [r.adverse_r for r in records if r.adverse_r is not None]
    if not values:
        return SlippageSummary(count=0, mean_adverse_r=None,
                               median_adverse_r=None, worst_adverse_r=None,
                               favourable=0)
    return SlippageSummary(
        count=len(values),
        mean_adverse_r=statistics.fmean(values),
        median_adverse_r=statistics.median(values),
        worst_adverse_r=max(values),
        favourable=sum(1 for v in values if v < 0),
    )
