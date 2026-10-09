"""The desk officer's checks (WORKLIST §42). Deterministic on purpose.

Gemma writes the evening journal, but it does not decide what is wrong: a
check that can be talked out of an alarm is not a check. Everything here is
arithmetic over the per-task logs, the broker and the audit log; Gemma only
turns the findings into prose, and the findings are printed beside it.

PURE.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date

from scanner.models import Frozen
from scanner.ops.runlog import header_time

#: Runs each task should have logged by the evening check on a trading day.
EXPECTED_RUNS: dict[str, int] = {
    "tradingagents": 1, "scan": 14, "flatten": 4, "momentum": 1, "gemma": 1,
    "data": 1, "outcomes": 1, "weekahead": 1,
    # Extended-hours sync (§59), by the 13:45 check: 00:35-04:35 and 12:35, 13:35.
    "extsync": 7,
}
#: Case-SENSITIVE: this codebase shouts its failures ("FAILED", "NOT FLAT",
#: "FAILING OPEN"); lowercase "0 failed run(s)" is a routine count, not an alarm.
TROUBLE = re.compile(r"Traceback|\bFAILED\b|\bERROR\b|NOT FLAT|FAILING OPEN|rejected:")
#: A theme above this share of everything invested is flagged.
CONCENTRATION_PCT = 50.0


class Finding(Frozen):
    level: str          # problem | watch | ok
    text: str


def runs_on(day: date, logs: Mapping[str, str]) -> dict[str, int]:
    """{task: headers logged on `day`} from each task's own log text."""
    out = {}
    for task, text in logs.items():
        out[task] = sum(1 for line in text.splitlines()
                        if (t := header_time(line)) is not None and t.date() == day)
    return out


def missing_runs(counts: Mapping[str, int], expected: Mapping[str, int] = EXPECTED_RUNS
                 ) -> list[Finding]:
    return [Finding(level="problem",
                    text=f"{task} ran {counts.get(task, 0)} of {n} expected times today")
            for task, n in expected.items() if counts.get(task, 0) < n]


def trouble_lines(day: date, logs: Mapping[str, str], limit: int = 3) -> list[Finding]:
    """Error-looking lines inside today's runs, per task."""
    found: list[Finding] = []
    for task, text in logs.items():
        today, hits = False, []
        for line in text.splitlines():
            t = header_time(line)
            if t is not None:
                today = t.date() == day
            elif today and TROUBLE.search(line):
                hits.append(line.strip()[:140])
        for line in hits[:limit]:
            found.append(Finding(level="problem", text=f"{task}: {line}"))
    return found


def exposure(values: Mapping[str, float], sector_of: Mapping[str, str]
             ) -> tuple[dict[str, float], list[Finding]]:
    """Share of invested dollars per sector across ALL books, and a finding
    for any sector over CONCENTRATION_PCT. The per-book sector caps cannot see
    this: the same theme in three books is one bet."""
    total = sum(values.values())
    if total <= 0:
        return {}, []
    by = Counter()
    for symbol, value in values.items():
        by[sector_of.get(symbol) or "unknown (non-S&P listing)"] += value
    shares = {k: v / total * 100 for k, v in by.most_common()}
    flags = [Finding(level="watch",
                     text=f"{k} is {pct:.0f}% of everything invested, across all books")
             for k, pct in shares.items() if pct > CONCENTRATION_PCT]
    return shares, flags


def books(positions: Iterable[Mapping], owner_of: Mapping[str, str]) -> dict[str, dict]:
    """Per book: positions, market value, unrealized P/L in dollars."""
    out: dict[str, dict] = {}
    for p in positions:
        book = owner_of.get(str(p.get("symbol")), "unknown")
        row = out.setdefault(book, {"positions": 0, "value": 0.0, "unrealized": 0.0})
        row["positions"] += 1
        row["value"] += float(p.get("market_value") or 0)
        row["unrealized"] += float(p.get("unrealized_pl") or 0)
    return out
