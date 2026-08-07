"""Swing structure — the pivots every price-structure pattern is built from.

A swing high is a bar whose high exceeds the `k` bars either side of it; a
swing low is its mirror. Everything in the Smart Money Concepts vocabulary —
liquidity sweep, change of character, break of structure — is a statement
about the sequence of these points, so they have to be right before any of it
means anything.

TWO THINGS THAT MAKE THIS HONEST

1. A SWING IS NOT KNOWN WHEN IT HAPPENS. Bar i cannot be called a swing high
   until bar i+k has printed — that is what "k bars either side" means. Read
   a chart after the fact and every pivot is obvious; trade it live and each
   one arrives k bars late. `known_at` is the only function a backtest may
   use, and it exists so that the delay cannot be forgotten. Marking pivots
   without it is the structural equivalent of buying at the screen-date close.

2. k IS A PARAMETER, NOT A FACT. There is no true swing size. k=2 and k=3
   produce different pivots and therefore different patterns on the same
   chart. It is exposed everywhere rather than baked in, so the backtest can
   measure how much the answer depends on it — a pattern that only works at
   one value of k has been fitted, not found.

Strict inequality throughout: a bar that merely ties with its neighbours is
not a pivot. Ties are common on thin intraday bars, and admitting them turns
flat congestion into a forest of imaginary structure.
"""

from __future__ import annotations

from typing import Sequence

from scanner.models import Frozen

#: Bars either side required to confirm a pivot. Two is the common default.
DEFAULT_K = 2

HIGH = "high"
LOW = "low"


class Swing(Frozen):
    """One confirmed pivot."""

    index: int
    price: float
    kind: str

    @property
    def is_high(self) -> bool:
        return self.kind == HIGH

    def confirmed_at(self, k: int = DEFAULT_K) -> int:
        """The bar by which this pivot could first have been known."""
        return self.index + k


def _pivots(values: Sequence[float], k: int, higher: bool) -> tuple[int, ...]:
    if k < 1 or len(values) < 2 * k + 1:
        return ()
    found: list[int] = []
    for i in range(k, len(values) - k):
        centre = values[i]
        neighbours = range(i - k, i + k + 1)
        if higher:
            if all(centre > values[j] for j in neighbours if j != i):
                found.append(i)
        elif all(centre < values[j] for j in neighbours if j != i):
            found.append(i)
    return tuple(found)


def swing_highs(highs: Sequence[float], k: int = DEFAULT_K) -> tuple[int, ...]:
    """Indices of bars whose high beats the k bars either side."""
    return _pivots(highs, k, higher=True)


def swing_lows(lows: Sequence[float], k: int = DEFAULT_K) -> tuple[int, ...]:
    """Indices of bars whose low undercuts the k bars either side."""
    return _pivots(lows, k, higher=False)


def swings(
    highs: Sequence[float], lows: Sequence[float], k: int = DEFAULT_K
) -> tuple[Swing, ...]:
    """Every pivot, oldest first.

    A bar can be both a swing high and a swing low — an outside bar that
    engulfs its neighbours in both directions. Both are reported; deciding
    what that means is the pattern's job, not this function's.
    """
    points = [Swing(index=i, price=highs[i], kind=HIGH) for i in swing_highs(highs, k)]
    points += [Swing(index=i, price=lows[i], kind=LOW) for i in swing_lows(lows, k)]
    return tuple(sorted(points, key=lambda s: (s.index, s.kind)))


def known_at(
    points: Sequence[Swing], index: int, k: int = DEFAULT_K
) -> tuple[Swing, ...]:
    """The pivots a trader standing on bar `index` could actually have seen.

    Confirmation lags the pivot by k bars. Every backtest and every live
    signal reads structure through this function; reading `swings` directly
    is a lookahead bug wearing a helpful name.
    """
    return tuple(s for s in points if s.confirmed_at(k) <= index)


def last_swing(points: Sequence[Swing], kind: str) -> Swing | None:
    """Most recent pivot of one kind, or None."""
    matching = [s for s in points if s.kind == kind]
    return matching[-1] if matching else None


def took_out(low: float, prior: Swing | None) -> bool:
    """Whether a low traded through a prior swing low — a liquidity sweep.

    Strictly below: touching the level to the tick is not taking it out, and
    the whole idea of the sweep is that resting orders below it got filled.
    """
    return prior is not None and prior.kind == LOW and low < prior.price


def broke_above(high: float, prior: Swing | None) -> bool:
    """Whether a high traded through a prior swing high."""
    return prior is not None and prior.kind == HIGH and high > prior.price
