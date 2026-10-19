"""Grades the morning leaders report's lists forward (operator, 2026-10-18).

RULES, FIXED BEFORE THE FIRST GRADE
- Each weekday the report records two lists of up to 4 names for decision day d
  (the ET date it ran, before the open): "rising" (12-1 top 20, above the
  50-day, up on the month) and "flow" (12-1 top 50 with money flowing in,
  scanner.features.money_flow).
- A list's return on d over h sessions: the equal-weight mean of
  selection.forward_return (open of d to the close h-1 sessions later).
- EXCESS = that minus the S&P 500's return over the same sessions.
  FLOW EDGE = flow's return minus rising's on the same day.
- Graded at h = 5 and 21 once the sessions have passed; a day with an empty
  list is skipped for that list. Overlapping horizons overstate the t-statistic
  (as in selection.py); it is for orientation only.
- Nothing trades on either list. After the Oct 31 verdict the operator can
  decide, from these numbers, whether Gemma should see money flow (option B).
PURE.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from scanner.models import Frozen
from scanner.ops.selection import Bars, Summary, forward_return, summarize

LISTS = ("rising", "flow")
HORIZONS = (5, 21)


class Grade(Frozen):
    horizon: int
    excess: dict[str, Summary]
    flow_edge: Summary


def list_return(symbols: Sequence[str], bars: Mapping[str, Bars], day: date,
                horizon: int) -> float | None:
    """Equal-weight forward return of `symbols`; None if any has not matured
    or the list is empty (a partial list would grade only the survivors)."""
    if not symbols:
        return None
    fwd = [forward_return(bars.get(s, ()), day, horizon) for s in symbols]
    if any(f is None for f in fwd):
        return None
    return sum(fwd) / len(fwd)


def grade(records: Sequence[Mapping], bars: Mapping[str, Bars], benchmark: Bars,
          horizon: int) -> Grade:
    excess: dict[str, list[float | None]] = {name: [] for name in LISTS}
    edge: list[float | None] = []
    for rec in records:
        day = date.fromisoformat(rec["day"])
        bench = forward_return(benchmark, day, horizon)
        rets = {name: list_return(rec["lists"].get(name, []), bars, day, horizon)
                for name in LISTS}
        for name in LISTS:
            r = rets[name]
            excess[name].append(r - bench if r is not None and bench is not None else None)
        if rets["flow"] is not None and rets["rising"] is not None:
            edge.append(rets["flow"] - rets["rising"])
    return Grade(horizon=horizon, excess={n: summarize(v) for n, v in excess.items()},
                 flow_edge=summarize(edge))


def symbols_needed(records: Sequence[Mapping]) -> set[str]:
    return {s for rec in records for names in rec["lists"].values() for s in names}
