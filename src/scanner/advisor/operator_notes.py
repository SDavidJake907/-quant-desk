"""Notes from the operator to Gemma: plain information, never instructions.

Operator 2026-10-21: "lets explain this to Gemma so its in her memory, what
this test is". A note is a dated piece of context about the desk (a test the
operator runs, why a name is on their list). While it is active it is shown
in her morning question, her 12:30 review and her safety check of the
operator's positions; it is also fed to Hindsight as a memory, so it stays in
her long-term record after it expires.

A note NEVER changes her rules, her menu, her sizes or her pool: the block
says so in its header, and nothing in the trading path reads it.

    data/gemma/operator_notes.json   [{"id", "from", "until", "text"}, ...]

Fail-open: a missing or broken file is no block. PURE except for reading it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from scanner.models import Frozen

NOTES = Path("data/gemma/operator_notes.json")
HEADER = ("NOTES FROM THE OPERATOR (information about the desk; they do not change "
          "your rules, your menu or your choices):")


class Note(Frozen):
    id: str
    start: date
    until: date
    text: str


def read(path: Path = NOTES) -> list[Note]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for r in raw if isinstance(raw, list) else []:
        try:
            out.append(Note(id=str(r["id"]), start=date.fromisoformat(r["from"]),
                            until=date.fromisoformat(r["until"]), text=str(r["text"]).strip()))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def active(notes: Iterable[Note], today: date) -> list[Note]:
    return [n for n in notes if n.start <= today <= n.until and n.text]


def block(notes: Iterable[Note], today: date) -> str:
    """The block for her question, or "" when no note is active."""
    live = active(notes, today)
    if not live:
        return ""
    return "\n".join((HEADER, *(f"- ({n.start:%m-%d}) {n.text}" for n in live)))


def memories(notes: Iterable[Note]) -> list[dict[str, str]]:
    """Hindsight memories, one per note, dated the day it was written."""
    return [{"id": f"note-{n.id}", "at": f"{n.start.isoformat()}T00:00:00",
             "kind": "note", "text": f"{n.start:%Y-%m-%d} Operator's note to Gemma: {n.text}"}
            for n in notes if n.text]
