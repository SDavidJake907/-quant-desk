"""Grade the unproven opinions against what the stock did next (WORKLIST §38).

THE RULE, FIXED BEFORE THE FIRST GRADE (2026-10-06)
A call is its direction: +1 (buy/overweight/hold-long), -1 (sell/underweight/
exit), 0 (hold) which is not graded. Its score is direction x (the stock's
return minus the S&P 500's) from the call date's close to the close
HORIZON_DAYS trading days later. One call per opinion, symbol and day (the
last). A call whose horizon has not passed is PENDING, never guessed.

Graduation: GRADUATION_CALLS graded calls and mean score with t >=
GRADUATION_T. The DISAGREEMENT column scores only calls against the primary
rule (a sell on a momentum pick): the one place an opinion could change a
decision.

PURE. Prices come in as {symbol: [(date, close), ...]}.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from datetime import date

from scanner.advisor.opinions import (GRADUATION_CALLS, GRADUATION_T, HORIZON_DAYS,
                                      SOURCES)
from scanner.models import Frozen

Series = Sequence[tuple[date, float]]


class Call(Frozen):
    opinion: str
    symbol: str
    day: date
    direction: int


class Record(Frozen):
    opinion: str
    graded: int
    pending: int
    mean: float
    t: float
    disagreements: int
    disagreement_mean: float

    @property
    def graduated(self) -> bool:
        return self.graded >= GRADUATION_CALLS and self.t >= GRADUATION_T


def calls_from_rows(rows: Iterable[Mapping]) -> list[Call]:
    """Directional calls, the last per (opinion, symbol, day)."""
    latest: dict[tuple[str, str, date], Call] = {}
    for row in rows:
        source = SOURCES.get(str(row.get("event")))
        if not source:
            continue
        key, parse = source
        direction = parse(str(row.get("reason", "")))
        symbol = str(row.get("symbol", "")).upper()
        try:
            day = date.fromisoformat(str(row.get("at", ""))[:10])
        except ValueError:
            continue
        if direction is None or not symbol:
            continue
        latest[(key, symbol, day)] = Call(opinion=key, symbol=symbol, day=day,
                                          direction=direction)
    return [c for c in latest.values() if c.direction != 0]


def forward_return(series: Series, day: date, horizon: int) -> float | None:
    """Close on the last session <= day, to `horizon` sessions later; None
    if that later close does not exist yet."""
    days = [d for d, _ in series]
    i = bisect_right(days, day) - 1
    if i < 0 or i + horizon >= len(series):
        return None
    start, end = series[i][1], series[i + horizon][1]
    return end / start - 1.0 if start else None


def _mean_t(xs: list[float]) -> tuple[float, float]:
    if not xs:
        return 0.0, 0.0
    mean = sum(xs) / len(xs)
    if len(xs) < 2:
        return mean, 0.0
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    se = math.sqrt(var / len(xs))
    return mean, (mean / se if se else 0.0)


def grade(calls: Iterable[Call], prices: Mapping[str, Series], bench: Series,
          picks_on: Mapping[date, frozenset[str]] | None = None,
          horizon: int = HORIZON_DAYS) -> dict[str, Record]:
    """A Record per opinion. `picks_on(day)` gives the momentum picks in force
    that day, for the disagreement column; omit it and that column is empty."""
    picks_on = picks_on or {}
    pick_days = sorted(picks_on)
    scores: dict[str, list[float]] = {}
    against: dict[str, list[float]] = {}
    pending: dict[str, int] = {}
    for c in calls:
        scores.setdefault(c.opinion, [])
        stock = forward_return(prices.get(c.symbol, ()), c.day, horizon)
        market = forward_return(bench, c.day, horizon)
        if stock is None or market is None:
            pending[c.opinion] = pending.get(c.opinion, 0) + 1
            continue
        score = c.direction * (stock - market) * 100.0
        scores[c.opinion].append(score)
        i = bisect_right(pick_days, c.day) - 1
        in_force = picks_on[pick_days[i]] if i >= 0 else frozenset()
        if c.direction == -1 and c.symbol in in_force:
            against.setdefault(c.opinion, []).append(score)
    out = {}
    for name, xs in scores.items():
        mean, t = _mean_t(xs)
        dis = against.get(name, [])
        out[name] = Record(opinion=name, graded=len(xs), pending=pending.get(name, 0),
                           mean=mean, t=t, disagreements=len(dis),
                           disagreement_mean=_mean_t(dis)[0])
    return out
