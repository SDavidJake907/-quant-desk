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
