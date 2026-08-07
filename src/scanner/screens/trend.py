"""Trend-continuation screen.

WHAT THIS IS: a description of a stock's current state, not a prediction.
Every criterion below is a measurable fact about price history. None of them
say what happens next.

Provenance, so you know how much to trust each part:
  - 12-1 momentum has real academic support (Jegadeesh & Titman, 1993) and has
    survived out-of-sample testing across decades and markets. It is the
    strongest single ingredient here.
  - The moving-average stack ("trend template") is practitioner heuristic,
    popularised by Weinstein / O'Neil / Minervini. Widely used, far less
    rigorously validated. Treat it as a filter, not as evidence.
  - Volume dry-up and range tightness are folklore with plausible mechanics
    and weak published support.

Until this is backtested on point-in-time data, none of it is validated on
this universe. The ranking is a to-research list, not a buy list.
"""

from __future__ import annotations

from scanner.features.technicals import Features
from scanner.models import Frozen


class Criterion(Frozen):
    """One pass/fail test, kept named so results stay explainable."""

    name: str
    passed: bool
    detail: str


class Candidate(Frozen):
    """A screened symbol with its criteria and score."""

    symbol: str
    features: Features
    relative_strength: float | None
    criteria: tuple[Criterion, ...]
    score: float

    @property
    def passed_all(self) -> bool:
        return all(c.passed for c in self.criteria)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.criteria if c.passed)


def _criteria(f: Features, rs: float | None) -> tuple[Criterion, ...]:
    price = f.price
    checks: list[Criterion] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(Criterion(name=name, passed=passed, detail=detail))

    add(
        "price > 50d MA",
        f.sma50 is not None and price > f.sma50,
        f"{price:,.2f} vs {f.sma50:,.2f}" if f.sma50 else "insufficient history",
    )
    add(
        "50d > 150d > 200d",
        all(v is not None for v in (f.sma50, f.sma150, f.sma200))
        and f.sma50 > f.sma150 > f.sma200,
        f"{f.sma50:,.0f} / {f.sma150:,.0f} / {f.sma200:,.0f}"
        if all(v is not None for v in (f.sma50, f.sma150, f.sma200))
        else "insufficient history",
    )
    add(
        "200d MA rising",
        f.sma200_slope_pct is not None and f.sma200_slope_pct > 0,
        f"{f.sma200_slope_pct:+.2f}% over 1m" if f.sma200_slope_pct is not None else "n/a",
    )
    add(
        "within 25% of 52w high",
        f.pct_from_52w_high > -25.0,
        f"{f.pct_from_52w_high:+.1f}% from high",
    )
    add(
        "30%+ above 52w low",
        f.pct_above_52w_low > 30.0,
        f"{f.pct_above_52w_low:+.1f}% above low",
    )
    add(
        "outperforming index",
        rs is not None and rs > 0,
        f"{rs:+.1f}pp vs benchmark" if rs is not None else "n/a",
    )
    return tuple(checks)


def _score(f: Features, rs: float | None, criteria: tuple[Criterion, ...]) -> float:
    """Composite rank. Weighted toward the criterion with the best evidence.

    Deliberately simple and readable: an opaque score you cannot audit is worse
    than a crude one you can.
    """
    score = 0.0

    # Relative strength carries the most weight (best-supported signal).
    if rs is not None:
        score += max(min(rs, 100.0), -100.0) * 0.5

    # Structural criteria: modest fixed credit each.
    score += sum(8.0 for c in criteria if c.passed)

    # Proximity to highs: closer is better, capped.
    score += max(f.pct_from_52w_high, -25.0) * 0.4

    # Coiling bonus: tight recent range plus volume dry-up.
    if f.range_tightness is not None and f.range_tightness < 6.0:
        score += 5.0
    if f.volume_ratio is not None and f.volume_ratio < 0.9:
        score += 3.0

    return score


def evaluate(f: Features, rs: float | None) -> Candidate:
    criteria = _criteria(f, rs)
    return Candidate(
        symbol=f.symbol,
        features=f,
        relative_strength=rs,
        criteria=criteria,
        score=_score(f, rs, criteria),
    )


def rank(
    candidates: tuple[Candidate, ...], *, require_all: bool = True
) -> tuple[Candidate, ...]:
    """Filter then sort. `require_all=False` shows near-misses too."""
    pool = tuple(c for c in candidates if c.passed_all) if require_all else candidates
    return tuple(sorted(pool, key=lambda c: c.score, reverse=True))
