"""Selection skill, graded across the whole menu (WORKLIST §47).

THE IDEA (Jansen, ML for Algorithmic Trading, ch. 4-5; the fundamental law of
active management: skill shows as forecast quality x sqrt(number of bets))
Gemma holds 5 names, so its P/L needs months to tell skill from luck. But every
morning it chooses 5 of ~35: grading the CHOSEN against the PASSED-OVER names
uses all ~35 forecasts a day. The same applies to TradingAgents' ~46 ratings.

RULES, FIXED BEFORE THE FIRST GRADE (2026-10-07)
- Forward return of a name on decision day d over h sessions: open of d (the
  first price after a 05:45 decision) to the close h-1 sessions later.
- Gemma, per day: SPREAD = mean forward return of the chosen minus that of the
  passed-over names (percentage points). Days are graded only once h sessions
  have passed.
- TradingAgents, per day: Spearman rank correlation (IC) between its call
  (+1 buy/overweight, 0 hold, -1 sell/underweight) and the forward returns.
- Summary: mean, t-statistic across days, share of days positive. Horizons
  overlap day to day, so the t-statistic overstates certainty; it is shown
  for orientation, and judged only with the pre-set graduation rule.
PURE.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date

from scanner.models import Frozen

Bars = Sequence[tuple[date, float, float]]          # (day, open, close), oldest first


def forward_return(bars: Bars, day: date, horizon: int) -> float | None:
    """% from the open of `day` to the close `horizon`-1 sessions later; None
    if `day` is missing or the horizon has not passed."""
    days = [b[0] for b in bars]
    if day not in days:
        return None
    i = days.index(day)
    if i + horizon - 1 >= len(bars):
        return None
    start, end = bars[i][1], bars[i + horizon - 1][2]
    return (end / start - 1) * 100 if start else None


def spread(chosen: Iterable[str], menu: Iterable[str], fwd: Mapping[str, float]) -> float | None:
    """Mean forward return of the chosen minus the passed-over, in points."""
    chosen = set(chosen)
    a = [fwd[s] for s in chosen if s in fwd]
    b = [fwd[s] for s in set(menu) - chosen if s in fwd]
    if not a or not b:
        return None
    return sum(a) / len(a) - sum(b) / len(b)


def _ranks(xs: Sequence[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    if len(a) < 3 or len(a) != len(b):
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return cov / (va * vb) if va and vb else None


class Summary(Frozen):
    days: int
    mean: float
    t: float
    positive_days: int


def summarize(values: Iterable[float | None]) -> Summary:
    xs = [v for v in values if v is not None]
    if not xs:
        return Summary(days=0, mean=0.0, t=0.0, positive_days=0)
    m = sum(xs) / len(xs)
    if len(xs) < 2:
        return Summary(days=1, mean=m, t=0.0, positive_days=int(m > 0))
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return Summary(days=len(xs), mean=m, t=(m / (sd / math.sqrt(len(xs))) if sd else 0.0),
                   positive_days=sum(x > 0 for x in xs))
