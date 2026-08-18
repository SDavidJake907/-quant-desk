"""Cross-sectional momentum: rank the universe, hold the top slice.

WHAT THIS IS, AND WHAT IT IS NOT
Jegadeesh and Titman (1993) sort a universe by trailing 12-month return
skipping the most recent month, buy the top decile, and rebalance monthly.
That is the construction behind three decades of out-of-sample evidence across
markets and asset classes, and every part of it is RELATIVE: a position exists
because the name sits near the top of a list, and it leaves when it does not.

`screens/momentum.py` in this repo tried to use the same ingredient as an entry
TRIGGER, with an ATR stop and a profit target. It lost to random entries in 38
of 40 configurations. The evidence never covered that strategy -- the two share
a name and almost nothing else. Hence a separate module: mixing them again is
the specific mistake worth engineering against.

WHY THERE ARE NO LEVELS HERE
No entry price, no stop, no target, no reward-risk. Momentum's return lives in
a right tail held over months; a stop truncates it, which this project has now
measured twice -- removing a fixed target alone roughly doubled per-trade
return. The exit rule is "no longer in the top slice at the next rebalance",
and it belongs to `backtest.engine`, not here.

WHAT THE SKIP MONTH IS FOR
`Features.momentum_12_1` already excludes the most recent month. Short-horizon
reversal runs against you -- last month's biggest winners tend to give some
back -- so including it measurably weakens the signal.
"""

from __future__ import annotations

from scanner.features.technicals import Features
from scanner.screens.trend import Candidate, Criterion

#: Sorts below any real momentum reading, including large negative ones, so a
#: name that cannot be ranked can never displace one that can.
UNRANKABLE = float("-inf")


def evaluate(f: Features, rs: float | None) -> Candidate:
    """Score one name by its 12-1 momentum.

    The single criterion is availability, not quality. This screen expresses no
    opinion about whether a stock is good -- only about where it sits in the
    ordering. Filtering on level rather than rank is what turns a factor back
    into a timing signal.
    """
    momentum = f.momentum_12_1
    available = momentum is not None
    criteria = (
        Criterion(
            name="12-1 momentum available",
            passed=available,
            detail=f"{momentum:+.1f}% over 12m ex-1m" if available
                   else f"only {f.bars_available} bars of history",
        ),
    )
    return Candidate(
        symbol=f.symbol,
        features=f,
        relative_strength=rs,
        criteria=criteria,
        score=momentum if available else UNRANKABLE,
    )
