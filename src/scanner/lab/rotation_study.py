"""Hold about three weeks, then rotate (operator 2026-10-23: "hold for the best 3 weeks.
and find others to replace? holding that long with only few positions wouldnt be that great").

Each buy is held a fixed number of trading days (10 / 15 / 21), then sold; every week the
empty slots are refilled from the top of the ranking -- the same stock again if it still
ranks there. 10 or 20 positions, and a 3-week variant with a 2.5-ATR safety stop. The seven
entries are the entry study's strongest (lab/entry_study.py). Counted (stage
"rotation_study"); compare each against the entry study's longer holds.
"""

from __future__ import annotations

from typing import Any

from scanner.lab.baselines import _r
from scanner.lab.entry_study import ENTRIES

ROTATION_ENTRIES = ("live-plan entry", "6-month leaders", "momentum 12-1", "momentum + market filter",
                    "dip in an uptrend", "relative strength 3m", "fresh golden cross")

#: (label, hold days, positions, stop ATR)
SHAPES: tuple[tuple[str, int, int, float], ...] = (
    ("2 weeks, 10 names", 10, 10, 0.0),
    ("3 weeks, 10 names", 15, 10, 0.0),
    ("4 weeks, 10 names", 21, 10, 0.0),
    ("2 weeks, 20 names", 10, 20, 0.0),
    ("3 weeks, 20 names", 15, 20, 0.0),
    ("4 weeks, 20 names", 21, 20, 0.0),
    ("3 weeks, 10 names + stop 2.5", 15, 10, 2.5),
    ("3 weeks, 20 names + stop 2.5", 15, 20, 2.5),
)


def grid() -> list[tuple[str, str, dict[str, Any]]]:
    entries = dict(ENTRIES)
    out = []
    for en in ROTATION_ENTRIES:
        for label, hold, n, stop in SHAPES:
            out.append((en, label, _r(**{**entries[en], "top_n": n, "rebalance_days": 5,
                                         "max_hold_days": hold, "stop_atr": stop})))
    return out
