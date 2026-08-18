"""Momentum swing setups: the trend screen, given levels.

WHAT THIS ADDS, AND WHAT IT DELIBERATELY DOES NOT
`screens/trend.py` ranks names by state. It emits a score and no prices, so
`execution.sizing` — which derives share count from stop distance — has nothing
to work with, and `backtest.setups.resolve` has no stop or target to test. This
module supplies an entry, a stop and a target, and changes not one criterion of
the screen itself. The selection rule and the management rule stay separable,
so a disappointing result can be attributed to one or the other.

WHY ATR SETS THE STOP
Risk is defined per name by that name's own volatility. A 2 x ATR stop on a
utility and on a semiconductor are the same bet in R terms, which is the only
thing that makes averaging R across a universe legitimate. A fixed percentage
stop instead systematically over-risks quiet names and under-risks volatile
ones, and the resulting average R describes no trade anybody took.

WHY A SIGNAL IS A TRANSITION, NOT A STATE
This is the trap that makes trend-following backtests look extraordinary. The
screen passes for as long as the trend holds, so reading every passing bar as a
signal turns one 60-day advance into 60 trades. They share a move, resolve
together, and are in no sense 60 independent observations — but they are
counted as 60, which shrinks every confidence interval by a factor of about
eight and makes noise look like an edge. A signal is the bar on which the
screen STARTS passing, and there is at most one per advance.

NOTHING HERE IS VALIDATED
The trend screen's own docstring calls it a to-research list. Adding levels
does not add evidence. Until this has been through a train split and a single
holdout, it must not reach `trade.py`.
"""

from __future__ import annotations

from scanner.features.bars import Bars
from scanner.features.technicals import (
    Features,
    average_true_range_pct,
    compute,
    relative_strength,
)
from scanner.models import Frozen
from scanner.screens import trend

#: Bars of history before the screen can be evaluated at all: a 200-day moving
#: average plus the month 12-1 momentum skips. Below this the screen is not
#: strict, it is uninformed.
MIN_HISTORY = 260

#: Stop distance in ATRs. Two is the common choice in the momentum literature;
#: it is a starting point to be measured, not a tuned value.
DEFAULT_ATR_MULTIPLE = 2.0

#: Target distance as a multiple of the stop distance.
DEFAULT_REWARD_RISK = 2.0

#: ATR window, in days.
ATR_WINDOW = 14


class SwingSetup(Frozen):
    """One momentum entry, with the levels it implies.

    Deliberately the same shape as `screens.smc.Setup` where it overlaps, so
    the same resolver, cost model and sizing code serve both strategies.
    """

    symbol: str
    trigger_index: int
    entry_price: float
    stop_price: float
    target_price: float
    atr_pct: float
    score: float
    relative_strength: float | None

    @property
    def risk(self) -> float:
        return self.entry_price - self.stop_price

    @property
    def reward(self) -> float:
        return self.target_price - self.entry_price

    @property
    def reward_risk(self) -> float:
        return self.reward / self.risk if self.risk > 0 else 0.0


def _features_at(bars: Bars, benchmark: Bars, index: int) -> tuple[Features, float | None]:
    """Features as they looked at `index`, and relative strength against the
    benchmark ON THE SAME DATE.

    `bars.upto` is what makes this point-in-time. Aligning the benchmark by
    date rather than by index matters because a stock that listed late, or was
    halted for a day, is not on the same bar count as the index.
    """
    history = bars.upto(index)
    features = compute(history)
    at = benchmark.index_of(bars.dates[index])
    if at is None:
        return features, None
    return features, relative_strength(features, compute(benchmark.upto(at)))


#: The six criteria `trend.evaluate` reports, by name. Kept here so a subset
#: can be validated against the real screen rather than against a guess.
CRITERIA = frozenset({
    "price > 50d MA",
    "50d > 150d > 200d",
    "200d MA rising",
    "within 25% of 52w high",
    "30%+ above 52w low",
    "outperforming index",
})


def _check(required: frozenset[str] | None) -> frozenset[str] | None:
    """Validate a criteria subset before it is used to select trades.

    An unrecognised name would silently drop a constraint, loosening the screen
    without saying so — and a loosened screen that produces a better number is
    the hardest kind of result to catch. An empty set is not a screen at all:
    it passes on the first bar with enough history and never signals again.
    """
    if required is None:
        return None
    unknown = required - CRITERIA
    if unknown:
        raise ValueError(f"unknown criteria: {sorted(unknown)}")
    if not required:
        raise ValueError("an empty criteria set is not a screen")
    return required


def passes(history: Bars, benchmark: Bars,
           required: frozenset[str] | None = None) -> bool:
    """Whether the trend screen passes on the last bar of `history`.

    Takes an already-truncated series so callers cannot accidentally hand it
    the future — the one mistake that would invalidate everything downstream.

    The benchmark is REQUIRED, not optional. "Outperforming the index" is one
    of the screen's six criteria and the one with the strongest published
    support; without a benchmark it evaluates to None and fails, so an optional
    argument would silently turn the whole screen off rather than relax it.
    """
    if len(history) < MIN_HISTORY:
        return False
    features = compute(history)
    at = benchmark.index_of(history.dates[-1])
    rs = None if at is None else relative_strength(features, compute(benchmark.upto(at)))
    candidate = trend.evaluate(features, rs)
    if required is None:
        return candidate.passed_all
    return all(c.passed for c in candidate.criteria if c.name in required)


def _setup_at(
    bars: Bars, benchmark: Bars, index: int, *,
    atr_multiple: float, reward_risk: float,
) -> SwingSetup | None:
    """Levels for a signal on `index`, or None if they cannot be formed."""
    history = bars.upto(index)
    atr_pct = average_true_range_pct(history, ATR_WINDOW)
    if not atr_pct or atr_pct <= 0:
        return None  # no volatility estimate means no defensible stop

    features, rs = _features_at(bars, benchmark, index)
    entry = bars.closes[index]
    risk = atr_multiple * (atr_pct / 100.0) * entry
    stop = entry - risk
    if risk <= 0 or stop <= 0:
        # A multiple wide enough to put the stop at or below zero is not a
        # trade. Sizing would divide by a non-positive risk and the R-multiple
        # would be meaningless.
        return None

    return SwingSetup(
        symbol=bars.symbol,
        trigger_index=index,
        entry_price=entry,
        stop_price=stop,
        target_price=entry + reward_risk * risk,
        atr_pct=atr_pct,
        score=trend.evaluate(features, rs).score,
        relative_strength=rs,
    )


def detect(
    bars: Bars,
    benchmark: Bars,
    *,
    atr_multiple: float = DEFAULT_ATR_MULTIPLE,
    reward_risk: float = DEFAULT_REWARD_RISK,
    required: frozenset[str] | None = None,
) -> tuple[SwingSetup, ...]:
    """Every momentum signal in the series, oldest first.

    One per advance: the bar where the screen starts passing. Each is built
    only from bars at or before its own trigger.

    `required` narrows the screen to a subset of its criteria, so each one can
    be measured on its own instead of only as part of the six-way AND.
    """
    required = _check(required)
    if len(bars) < MIN_HISTORY:
        return ()

    setups: list[SwingSetup] = []
    previous = False
    for index in range(MIN_HISTORY - 1, len(bars)):
        current = passes(bars.upto(index), benchmark, required)
        if current and not previous:
            setup = _setup_at(bars, benchmark, index,
                              atr_multiple=atr_multiple, reward_risk=reward_risk)
            if setup is not None:
                setups.append(setup)
        previous = current
    return tuple(setups)
