"""Committing less capital as the weekend approaches.

WHAT THIS IS NOT
It is not a weekday edge. Measured over 125 setups, expectancy by trigger day
runs Mon +0.539R, Tue +0.054R, Wed -0.562R, Thu +0.780R, Fri +1.068R, and a
permutation test puts the chance of a random five-way split producing a best
day at least that good at 18.9%. Train and holdout disagree about which day
wins. Sizing more on any particular weekday would be sizing on noise.

WHAT IT IS
A risk schedule. Capital committed falls as the market approaches a window in
which nothing can be managed, and reaches zero on Thursday where the wind-down
already refuses new positions. Exposure and return scale together on this
strategy — return over drawdown sits between 4.7 and 5.1 at every deployment
level tested — so this costs return in proportion and buys a smaller book going
into the weekend. That is a preference, legitimately held, not an improvement.

HOW IT LANDS IN THE SIZER
`sizing.size_position` derives shares from the stop and caps them by
`max_position_pct`. The taper scales that cap so a full book reaches the day's
target: five slots at a 10% cap is 50% deployed. Nothing else in the sizing
path changes, and the cap can only ever be reduced — a schedule asking for more
than 100% cannot widen a position beyond what was validated.
"""

from __future__ import annotations

from datetime import date

from scanner.execution.policy import TradingPolicy

#: Saturday is 5.
FIRST_WEEKEND_DAY = 5


def deployment_for(on: date, policy: TradingPolicy) -> float:
    """Share of equity that may be committed to new positions on `on`."""
    weekday = on.weekday()
    if weekday >= FIRST_WEEKEND_DAY:
        return 0.0
    schedule = policy.weekday_deployment_pct
    if weekday >= len(schedule):
        return 0.0
    return max(0.0, float(schedule[weekday]))


def position_cap_for(on: date, policy: TradingPolicy) -> float:
    """The per-position cap that makes a full book hit the day's target.

    Never above `max_position_pct`: the taper exists to reduce exposure, and a
    schedule that asked for more must not become a route to a larger position
    than the validated configuration allows.
    """
    target = deployment_for(on, policy)
    if target <= 0 or policy.max_positions < 1:
        return 0.0
    return min(policy.max_position_pct, target / policy.max_positions)
