"""What to hold and whether to hold anything: the ranking and the trend filter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import fmean

#: Measured settings (momentum_guard_test.py, arm A). Not tuned here.
TOP_N = 10
TREND_DAYS = 210


def rank(scores: Mapping[str, float | None], *, top_n: int = TOP_N) -> tuple[str, ...]:
    """The top `top_n` symbols by 12-1 momentum, best first.

    A symbol without a score (too little history) is never ranked. Ties break
    by symbol so two runs on the same data choose the same names.
    """
    rankable = [(score, symbol) for symbol, score in scores.items() if score is not None]
    rankable.sort(key=lambda pair: (-pair[0], pair[1]))
    return tuple(symbol for _, symbol in rankable[:top_n])


def trend_ok(index_closes: Sequence[float], *, days: int = TREND_DAYS) -> bool:
    """Whether the S&P 500's latest close is above its `days`-day average.

    False without enough history -- as in the backtest, no reading means the
    tranche sits in cash rather than guessing.
    """
    if len(index_closes) <= days:
        return False
    return index_closes[-1] > fmean(index_closes[-days:])
