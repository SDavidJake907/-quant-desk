"""Account-level hard stops: a day, a week, and the drop from the high.

Operator's limits, 2026-10-06 (GO_LIVE.md): 3% in a day, 6% in a week, 15%
below the equity high. Any one means "stop and review": no new entries until
a human has written down what happened. Pure arithmetic; risk_monitor.py
reads the broker and decides whether to enforce.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from scanner.models import Frozen


class Limits(Frozen):
    day_pct: float = 3.0
    week_pct: float = 6.0
    drawdown_pct: float = 15.0


class Breach(Frozen):
    name: str            # "day" | "week" | "drawdown"
    loss_pct: float
    limit_pct: float


def _loss(start: float, now: float) -> float:
    return round((start - now) / start * 100, 2) if start > 0 else 0.0


def evaluate(*, equity: float, day_start: float, week_start: float, peak: float,
             limits: Limits) -> list[Breach]:
    checks = (("day", _loss(day_start, equity), limits.day_pct),
              ("week", _loss(week_start, equity), limits.week_pct),
              ("drawdown", _loss(max(peak, equity), equity), limits.drawdown_pct))
    return [Breach(name=n, loss_pct=loss, limit_pct=lim) for n, loss, lim in checks if loss >= lim]


def week_start(history: Sequence[tuple[date, float]], *, today: date) -> float:
    """Equity at the last daily close before this week's Monday."""
    monday = today - timedelta(days=today.weekday())
    before = [eq for d, eq in history if d < monday]
    return before[-1] if before else history[0][1]
