"""The scheduled-run logs, one file per task, read back as one timeline.

WHY ONE FILE PER TASK (WORKLIST §40, 2026-10-06)
Every scheduled .bat used to append to logs/scheduled.log. cmd.exe opens a
`>>` target without write sharing, so while one task held the file, a second
task's commands -- header echo and python line alike -- failed to open it and
DID NOT RUN AT ALL, leaving no trace. Reproduced 2026-10-06; the weekend
flatten went missing five times in three days that way. Separate files remove
the shared lock. The old logs/scheduled.log stays as history and is read too.

PURE apart from `read_logs`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*m")
HEADER = re.compile(r"^===== (?:(.*?) )?\w{3} (\d{2}/\d{2}/\d{4})\s+(\d{1,2}:\d{2}:\d{2})")
PATTERN = "scheduled*.log"


def header_time(line: str) -> datetime | None:
    m = HEADER.match(ANSI.sub("", line))
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(2)} {m.group(3)}", "%m/%d/%Y %H:%M:%S")
    except ValueError:
        return None


def blocks(text: str) -> list[tuple[datetime, list[str]]]:
    """Runs in one file: each header and the lines under it. Lines before the
    first header belong to no run and are dropped."""
    out: list[tuple[datetime, list[str]]] = []
    for line in text.splitlines():
        when = header_time(line)
        if when is not None:
            out.append((when, [ANSI.sub("", line)]))
        elif out:
            out[-1][1].append(ANSI.sub("", line))
    return out


def merge(texts: Iterable[str]) -> list[str]:
    """Every run from every file, in start-time order, as one list of lines."""
    runs = [b for text in texts for b in blocks(text)]
    runs.sort(key=lambda b: b[0])           # stable: same-second runs keep file order
    return [line for _, lines in runs for line in lines]


def read_logs(folder: Path = Path("logs")) -> list[str]:
    texts = []
    for path in sorted(folder.glob(PATTERN)):
        try:
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return merge(texts)
