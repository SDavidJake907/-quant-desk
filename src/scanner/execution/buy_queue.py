"""Gemma's buys that wait for 10:00 ET (the plan, execution/satellite.py).

Her morning decision runs at 9:45 ET inside a 15-minute task, so it cannot wait for
10:00 itself. It writes the buys that passed every rule here; peak watch (every 5
minutes) sends them on its first regular-session pass from 10:00 ET, sized then.
A queue is good for its own day only: a stale decision is never bought tomorrow.

    data/gemma/buy_queue.json   {"day": "2026-10-26", "items": {symbol: plan}}

PURE: dicts in, new values out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from scanner.ops import shock_alert as sa

PATH = "data/gemma/buy_queue.json"


def make(day: date, items: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {"day": day.isoformat(), "items": {s: dict(v) for s, v in items.items()}}


def due(queue: Mapping[str, Any], *, today: date, held: Iterable[str],
        pending: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Today's queued buys not already held or working, in queue order."""
    if queue.get("day") != today.isoformat():
        return {}
    skip = set(held) | set(pending)
    return {s: dict(v) for s, v in (queue.get("items") or {}).items() if s not in skip}


def without(queue: Mapping[str, Any], done: Iterable[str]) -> dict[str, Any]:
    gone = set(done)
    return {**dict(queue), "items": {s: dict(v) for s, v in (queue.get("items") or {}).items()
                                     if s not in gone}}


#: THE 10:00 RECHECK (Gemma 2026-10-24, rules only, no model call): a queued buy is skipped when the
#: stock fell RECHECK_DROP_PCT or more since her 9:45 decision (she changed 3% to 2%: "a 2% drop in the
#: 45 minutes between decision and execution suggests a shift in immediate sentiment"), when a
#: bellwether / theme-shock alert that was NOT active at her decision now covers its sector, or when
#: earnings now fall inside the plan's buffer. Missing data skips that part (her 9:45 checks stand).
RECHECK_DROP_PCT = 2.0


def recheck(item: Mapping[str, Any], *, price: float | None, alert_kinds: Iterable[str],
            sector: str | None, earnings_in_days: int | None, buffer_days: int) -> str | None:
    """Why a queued buy is skipped at 10:00, or None to send it."""
    decided = item.get("price")
    if decided and price and (price / float(decided) - 1) * 100 <= -RECHECK_DROP_PCT:
        return (f"fell {(price / float(decided) - 1) * 100:+.1f}% since her 9:45 decision "
                f"({float(decided):,.2f} -> {price:,.2f}; the recheck skips -{RECHECK_DROP_PCT:g}% or more)")
    new = sorted(set(alert_kinds) & set(sa.OVERRIDES_MIN_HOLD) - set(item.get("alerts") or ()))
    if new and sector in sa.THEME_SECTORS:
        return f"a {' / '.join(new)} alert started after her decision and covers its sector ({sector})"
    if earnings_in_days is not None and earnings_in_days <= buffer_days:
        return f"earnings in {earnings_in_days} day(s) -- inside the plan's {buffer_days}-day buffer"
    return None
