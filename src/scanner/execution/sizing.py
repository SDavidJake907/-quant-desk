"""How many shares to buy.

RISK FIRST, SHARES SECOND
Size is derived from the stop, never chosen directly: decide what fraction of
equity a losing trade may cost, then let the stop distance determine the share
count. A 1% stop and a 4% stop then lose the same money when they fail, which
is the only reason an R-multiple means anything outside a spreadsheet.

Sizing by fixed dollars or fixed shares instead makes every backtest number in
this repo inapplicable to the account running it — the trades would no longer
be equally weighted, so the average R stops describing the average outcome.

THREE CAPS, ALL OF WHICH MUST HOLD
    risk budget      equity * risk_pct / stop distance
    position cap     no single name may exceed max_position_pct of equity
    buying power     you cannot spend money you do not have

The smallest wins. Fractional shares are not used: the result is floored, and
a setup too expensive for one share is simply skipped rather than rounded up.

THE CONCENTRATION TRAP, WHICH THE POSITION CAP EXISTS TO CATCH
Position size needed = risk_pct / stop_pct. That is arithmetic, and it is
brutal for tight stops: risking 1% of equity behind a 1.5% stop requires a
position worth 67% OF THE ACCOUNT in one name. Two such trades and you are
levered; five and a single gap can end you.

So on this strategy the position cap binds, not the risk budget, and the
effective risk per trade lands near 0.3% rather than the 1% asked for. That is
the cap doing its job. `limited_by` says so on every order rather than leaving
you to wonder why the size looks small — and if you raise the cap to "fix" it,
you are choosing concentration, not fixing anything.
"""

from __future__ import annotations

import math

from scanner.models import Frozen

#: Share of equity a single losing trade may cost.
DEFAULT_RISK_PCT = 1.0

#: Share of equity a single position may occupy, however tight its stop.
#: Without this, a 0.2% stop would size to 5x the account.
DEFAULT_MAX_POSITION_PCT = 20.0


class Sizing(Frozen):
    """The share count and why it came out that way."""

    shares: int
    risk_dollars: float
    position_dollars: float
    limited_by: str

    @property
    def is_tradeable(self) -> bool:
        return self.shares > 0


def size_position(
    equity: float,
    entry: float,
    stop: float,
    *,
    risk_pct: float = DEFAULT_RISK_PCT,
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
    buying_power: float | None = None,
) -> Sizing:
    """Shares to buy, or zero with a reason.

    `limited_by` names the binding constraint, so a surprisingly small order
    is explainable rather than mysterious.
    """
    if equity <= 0 or entry <= 0:
        return Sizing(shares=0, risk_dollars=0.0, position_dollars=0.0,
                      limited_by="no equity")
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return Sizing(shares=0, risk_dollars=0.0, position_dollars=0.0,
                      limited_by="stop is not below entry")

    by_risk = (equity * risk_pct / 100.0) / risk_per_share
    by_position = (equity * max_position_pct / 100.0) / entry
    power = equity if buying_power is None else buying_power
    by_power = max(0.0, power) / entry

    shares = math.floor(min(by_risk, by_position, by_power))
    if shares < 1:
        return Sizing(shares=0, risk_dollars=0.0, position_dollars=0.0,
                      limited_by="cannot afford one share")

    binding = min(
        (by_risk, "risk budget"),
        (by_position, "position cap"),
        (by_power, "buying power"),
        key=lambda pair: pair[0],
    )[1]
    return Sizing(
        shares=shares,
        risk_dollars=shares * risk_per_share,
        position_dollars=shares * entry,
        limited_by=binding,
    )
