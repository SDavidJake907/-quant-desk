"""Gemma's long-term memory through Hindsight (vectorize-io/hindsight), LIVE.

Operator 2026-10-19: "lets run it in the live, nothing shadowing".

The pipeline (scheduled 05:05 AK, before her 05:45 run):
  1 scripts/hindsight_feed.py (this venv) turns the audit log and her saved
    reviews into dated memories -> data/gemma/hindsight/feed.json
  2 scripts/hindsight_lessons.py (the separate hindsight-sandbox venv) retains
    the memories it has not seen, then asks Hindsight to reflect: lessons from
    her own trades, cited, nothing invented -> data/gemma/hindsight/lessons.json
  3 gemma_trade.py puts those lessons in her morning question (block() below),
    labelled so today's facts win when they disagree.

Tested 2026-10-19 before going live: recall 5/5 exact on her own history (the
09-24 text memory had ranked a stale announcement over its termination); the
effect on her decisions was within her own run-to-run variation, so this is
live on accuracy, not on a measured edge. Every lesson shown is logged with her
decision (gemma_decision.hindsight) so the mornings can be graded later.

Fail-open: no lessons file, an old one, or a broken one -> no block, and she
runs exactly as before. PURE except for reading the lessons file.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

#: Off = no lessons block (the 05:05 task still runs and writes its files).
ENABLED = True
DIR = Path("data/gemma/hindsight")
FEED = DIR / "feed.json"
LESSONS = DIR / "lessons.json"
#: The Sunday 16:45 run reads Friday's lessons; older than this is skipped.
MAX_AGE = timedelta(hours=72)
MAX_CHARS = 2500
#: The challenge started 2026-10-07; nothing earlier is fed.
SINCE = "2026-10-07"
HEADER = ("Lessons from your own trades since the challenge began (long-term memory, "
          "written from your decisions and results -- today's facts below win if they disagree):")


def _stamp(at: str) -> str:
    return str(at)[:16].replace("T", " ")


def memories(rows: Iterable[Mapping[str, Any]],
             reviews: Iterable[Mapping[str, Any]] = ()) -> list[dict[str, str]]:
    """Dated memories with stable ids, oldest first: her submitted decisions,
    her closed trades (gem- orders), positions closed on her behalf, her reviews."""
    out: list[dict[str, str]] = []
    for r in rows:
        at = str(r.get("at", ""))
        if at[:10] < SINCE:
            continue
        ev = r.get("event")
        if ev == "gemma_decision":
            try:
                b = json.loads(r.get("reason") or "{}")
            except ValueError:
                continue
            if not b.get("submitted"):
                continue
            reasons = " ".join(f"{s}: {w}" for s, w in (b.get("reasons") or {}).items())
            text = (f"{_stamp(at)} Gemma's decision ({b.get('session')}): held {b.get('keep')}; "
                    f"bought {b.get('buys') or 'nothing'}; sold {b.get('sells') or 'nothing'}. "
                    f"{reasons}" + (f" Journal: {b['journal']}" if b.get("journal") else ""))
            out.append({"id": f"decision-{at}", "at": at, "kind": "decision", "text": text[:1800]})
        elif ev == "trade_outcome":
            o = r.get("outcome") or {}
            if not str(o.get("client_order_id", "")).startswith("gem-"):
                continue
            try:
                text = (f"{_stamp(at)} Gemma's {o['symbol']} trade closed ({o.get('exit_reason')}): "
                        f"bought {o.get('shares')} at {float(o['entry_fill']):.2f}, sold at "
                        f"{float(o['exit_fill']):.2f}, {float(o['realized_dollars']):+.0f} dollars, "
                        f"{float(o['realized_r']):+.2f}R.")
            except (KeyError, TypeError, ValueError):
                continue
            out.append({"id": f"outcome-{o.get('client_order_id')}", "at": at, "kind": "outcome",
                        "text": text})
        elif ev == "gemma_exit":
            out.append({"id": f"exit-{r.get('symbol')}-{at}", "at": at, "kind": "exit",
                        "text": f"{_stamp(at)} Gemma's {r.get('symbol')} position was closed: "
                                f"{r.get('reason')}"})
    for rv in reviews:
        day = str(rv.get("day", ""))
        if day < SINCE:
            continue
        verdicts = " ".join(f"{p.get('symbol')} {p.get('verdict')}: {p.get('reason', '')}"
                            for p in rv.get("positions") or [])
        out.append({"id": f"review-{day}", "at": str(rv.get("at") or day), "kind": "review",
                    "text": f"{day} Gemma's evening review: {rv.get('summary', '')} {verdicts}"[:1800]})
    return sorted(out, key=lambda m: m["at"])


def block(lessons: Mapping[str, Any] | None, *, now: datetime) -> str:
    """The lessons block for her question, or "" when off, missing, stale or empty."""
    if not ENABLED or not lessons:
        return ""
    text = str(lessons.get("text") or "").strip()
    try:
        written = datetime.fromisoformat(str(lessons.get("at")))
    except ValueError:
        return ""
    if not text or now - written > MAX_AGE:
        return ""
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit("\n", 1)[0] + "\n(...)"
    return f"{HEADER}\n{text}"


def read_lessons(path: Path = LESSONS) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
