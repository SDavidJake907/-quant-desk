"""Grading Gemma's 5-day forecasts (WORKLIST §65).

Each morning she gives, for every stock on her list, the probability it beats
the S&P 500 over the next HORIZON trading days. A forecast is graded once those
days have closed: from the open of the decision day's session to the close of
the HORIZON-th session, stock against index.

The score is the Brier score -- the mean of (prob - outcome)^2 -- against 0.25,
which is what always answering 0.5 earns. Below 0.25 means her probabilities
carry information; above means she would do better saying nothing. It is the
first measure of her JUDGEMENT apart from the luck in her P/L.

PURE: decisions and bars in, grades and text out.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from scanner.features.bars import Bars
from scanner.models import Frozen

HORIZON = 5
#: Below this many graded forecasts the score is noise and says so.
MIN_GRADED = 30
NO_VIEW_BRIER = 0.25


class Forecast(Frozen):
    day: str
    symbol: str
    prob: float


class Graded(Frozen):
    day: str
    symbol: str
    prob: float
    excess: float          # stock minus S&P 500 return, % points

    @property
    def beat(self) -> bool:
        return self.excess > 0


def extract(rows: Iterable[Mapping[str, Any]]) -> list[Forecast]:
    """Forecasts from her submitted decisions; the last decision of a day wins."""
    by_day: dict[str, dict[str, float]] = {}
    for r in rows:
        if r.get("event") != "gemma_decision":
            continue
        try:
            body = json.loads(r.get("reason") or "{}")
        except (TypeError, ValueError):
            continue
        f = body.get("forecasts") if isinstance(body, dict) else None
        if body.get("submitted") and isinstance(f, dict) and f:
            by_day[str(r.get("at", ""))[:10]] = f
    return [Forecast(day=d, symbol=s, prob=float(p))
            for d in sorted(by_day) for s, p in by_day[d].items()]


def _start(bars: Bars, day: str) -> int | None:
    for i, d in enumerate(bars.dates):
        if d.isoformat() >= day:
            return i
    return None


def excess(stock: Bars, spx: Bars, day: str, horizon: int = HORIZON) -> float | None:
    """Stock minus index return (% points) over `horizon` sessions from `day`'s open,
    or None until those sessions have closed."""
    i, j = _start(stock, day), _start(spx, day)
    if i is None or j is None or stock.dates[i] != spx.dates[j]:
        return None
    a, b = i + horizon - 1, j + horizon - 1
    if a >= len(stock.closes) or b >= len(spx.closes) or not stock.opens[i] or not spx.opens[j]:
        return None
    return ((stock.closes[a] / stock.opens[i]) - (spx.closes[b] / spx.opens[j])) * 100


def grade(forecasts: Sequence[Forecast], bars: Mapping[str, Bars], spx: Bars) -> list[Graded]:
    out = []
    for f in forecasts:
        b = bars.get(f.symbol)
        e = excess(b, spx, f.day) if b is not None else None
        if e is not None:
            out.append(Graded(day=f.day, symbol=f.symbol, prob=f.prob, excess=e))
    return out


def score(graded: Sequence[Graded]) -> dict[str, Any]:
    n = len(graded)
    if not n:
        return {"n": 0}
    brier = sum((g.prob - (1.0 if g.beat else 0.0)) ** 2 for g in graded) / n
    called = [g for g in graded if g.prob != 0.5]
    hits = sum(1 for g in called if (g.prob > 0.5) == g.beat)
    buckets = {}
    for lo, hi in ((0.0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 1.01)):
        inside = [g for g in graded if lo <= g.prob < hi]
        if inside:
            buckets[f"{lo:.1f}-{min(hi, 1.0):.1f}"] = {
                "n": len(inside), "beat": sum(g.beat for g in inside) / len(inside)}
    return {"n": n, "brier": brier, "brier_no_view": NO_VIEW_BRIER,
            "skill": 1 - brier / NO_VIEW_BRIER,
            "hit_rate": hits / len(called) if called else None,
            "buckets": buckets}


def text(sc: Mapping[str, Any]) -> str:
    n = sc.get("n", 0)
    if n < MIN_GRADED:
        return (f"Your 5-day forecasts: {n} graded so far -- not enough to judge yet "
                f"(needs {MIN_GRADED}).")
    hit = sc.get("hit_rate")
    return (f"Your 5-day forecasts: {n} graded, Brier score {sc['brier']:.3f} "
            f"(always saying 0.5 scores 0.25; lower is better)"
            + (f", right side of 0.5 on {hit * 100:.0f}% of calls" if hit is not None else "")
            + ".")
