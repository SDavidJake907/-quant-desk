"""Keep the headlines the model was shown, so its judgment can be re-run.

WHY THIS EXISTS
`NewsItem.fetched_at` has always been documented as "Point-in-time stamp for
backtests" and nothing ever wrote one to disk. Yahoo's RSS feed returns only
what is current, so every headline the ranker read was discarded the moment
the scan ended.

The consequence is narrow and total: the model's ranking can be SCORED going
forward, because `candidate_slate` records its output and bars determine the
outcome — but it can never be RE-RUN against the past, because re-running
needs the input. A prompt change therefore cannot be tested on history. The
triage prompt was edited on 2026-09-24 and the only honest way to measure it
was fourteen hand-written cases.

WHAT IS STORED, AND WHAT IS NOT
Only the items actually shown to the model — the candidates a scan ranked.
Storing all 501 symbols' feeds every half hour would be a large pile of text
nobody asked a question of. The point is reproducing a DECISION, and a
decision only ever saw its candidates.

APPEND-ONLY, AND DEDUPLICATED WITHIN A DAY
One file per day. A scan runs fourteen times and mostly re-reads the same
headlines, so items already stored for that day are skipped on `id`. The file
grows with new stories rather than with scans.

Never rewrite a day's file. Its value is that it records what was visible at
the time, and a file that can be revised afterwards cannot prove that.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from scanner.models import NewsItem

DEFAULT_ROOT = Path("data/news")


def day_path(on: date, root: Path | str = DEFAULT_ROOT) -> Path:
    return Path(root) / f"{on.isoformat()}.jsonl"


def stored_ids(on: date, root: Path | str = DEFAULT_ROOT) -> set[str]:
    """Item ids already recorded for `on`.

    A missing or damaged file reads as empty rather than raising: losing a
    day's dedup costs duplicate lines, and failing here would cost the scan.
    """
    path = day_path(on, root)
    if not path.exists():
        return set()
    found: set[str] = set()
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    found.add(str(json.loads(line).get("id", "")))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return set()
    return found


def append_items(
    items: Iterable[NewsItem],
    *,
    on: date | None = None,
    root: Path | str = DEFAULT_ROOT,
) -> int:
    """Record items not already stored for the day. Returns how many were new.

    Never raises. This runs inside the scan, and a storage failure must not
    stop a trading decision — the headlines are evidence about the decision,
    not an input to it.
    """
    try:
        items = tuple(items)
        if not items:
            return 0
        when = on or datetime.now().astimezone().date()
        path = day_path(when, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        seen = stored_ids(when, root)

        written = 0
        with path.open("a", encoding="utf-8") as handle:
            for item in items:
                if not item.id or item.id in seen:
                    continue
                seen.add(item.id)
                handle.write(json.dumps(item.model_dump(mode="json"),
                                        sort_keys=True))
                handle.write("\n")
                written += 1
        return written
    except (OSError, TypeError, ValueError):
        return 0


def load_day(on: date, root: Path | str = DEFAULT_ROOT) -> tuple[NewsItem, ...]:
    """Everything recorded for one day, for replaying a decision."""
    path = day_path(on, root)
    if not path.exists():
        return ()
    out: list[NewsItem] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(NewsItem.model_validate(json.loads(line)))
            except (json.JSONDecodeError, ValueError):
                continue
    return tuple(out)


def load_symbol(
    symbol: str, on: date, root: Path | str = DEFAULT_ROOT
) -> tuple[NewsItem, ...]:
    """One symbol's stored headlines for a day, in fetch order."""
    wanted = symbol.upper()
    return tuple(i for i in load_day(on, root)
                 if i.queried_ticker.upper() == wanted)
