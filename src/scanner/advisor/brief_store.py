"""Which daily brief Gemma reads (operator 2026-10-22: news three times a day).

scripts/week_ahead.py writes data/brief/YYYY-MM-DD.json at 03:00 (morning),
12:50 (afternoon, after the close) and 18:50 AK (evening, the overnight
session), each run replacing that date's file. Her passes run 24/5, so after
midnight AK the newest brief is filed under YESTERDAY's date. She reads the
newest brief written within MAX_AGE; older news must never pass for today's.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

#: Longest gap between briefs is 18:50 -> 03:00 AK (8 h 10 min); 12 h leaves room
#: for one late or failed run without ever reaching back a whole day.
MAX_AGE = timedelta(hours=12)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        brief = json.loads(path.read_text(encoding="utf-8"))
        written = datetime.fromisoformat(brief["written_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not brief.get("market_text") or written.tzinfo is None:
        return None
    return {**brief, "_written": written}


def latest(directory: Path, now: datetime) -> dict[str, Any] | None:
    """The newest brief for today or yesterday written within MAX_AGE, else None."""
    today = now.date()
    found = [b for d in (today, today - timedelta(days=1))
             if (b := _read(directory / f"{d.isoformat()}.json")) is not None
             and b.get("date") == d.isoformat() and timedelta(0) <= now - b["_written"] <= MAX_AGE]
    if not found:
        return None
    best = max(found, key=lambda b: b["_written"])
    return {k: v for k, v in best.items() if k != "_written"}


def age_text(brief: dict[str, Any], now: datetime) -> str:
    minutes = int((now - datetime.fromisoformat(brief["written_at"])).total_seconds() // 60)
    return f"written {minutes // 60} h {minutes % 60} min ago"
