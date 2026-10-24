"""Gemma's lab log: every recipe she ran, on which years, and what came back.

    data/lab/log.jsonl   one JSON object per run

Nothing is ever removed: the count of tries is part of every result's meaning
(TRIALS.md). The hidden years are run once per recipe, ever.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

LOG = Path("data/lab/log.jsonl")
SEARCH, HIDDEN_A, HIDDEN_B = "search", "hidden_a", "hidden_b"


def read(path: Path | None = None) -> list[dict[str, Any]]:
    out = []
    path = path or LOG
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def append(entry: Mapping[str, Any], path: Path | None = None) -> None:
    path = path or LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def universe_of(e: Mapping[str, Any]) -> str:
    """Which stocks a run used; runs from before the size groups were big caps."""
    return str(e.get("universe") or "large")


def tried(entries: Iterable[Mapping[str, Any]], stage: str, *, universe: str | None = None) -> set[str]:
    return {e["key"] for e in entries if e.get("stage") == stage
            and (universe is None or universe_of(e) == universe)}


def nominated_since(entries: Iterable[Mapping[str, Any]], universe: str, since: datetime) -> bool:
    """Whether `universe` already had a hidden-years run since `since`: a weekend run that is
    restarted (a reboot, the scheduled task's next trigger) must not nominate twice."""
    for e in entries:
        if e.get("stage") != HIDDEN_A or universe_of(e) != universe:
            continue
        try:
            if datetime.fromisoformat(str(e["at"])) >= since:
                return True
        except (KeyError, ValueError):
            continue
    return False


def _line(e: Mapping[str, Any]) -> str:
    r = e["result"]
    rnd = r.get("random_annual_pct") or 0.0
    return (f"#{e['n']} {e['session']} [{e['key']}] {e['describe']} => {r['annual_pct']:+.1f}%/yr "
            f"({r['annual_pct'] - r['spx_annual_pct']:+.1f} pts vs index, {r['annual_pct'] - rnd:+.1f} vs random), "
            f"Sharpe {r['sharpe']:.2f}, DD {r['max_drawdown_pct']:.0f}%, {r['trades']} trades")


def summary(entries: list[Mapping[str, Any]], *, universe: str | None = None, best: int = 10,
            recent: int = 15) -> str:
    """Her log for the prompt: the hidden-years runs, her best searches, her latest --
    for one stock group when `universe` is given (other groups are counted in one line)."""
    others = ""
    if universe is not None:
        elsewhere = [e for e in entries if e.get("stage") == SEARCH and universe_of(e) != universe]
        if elsewhere:
            counts: dict[str, int] = {}
            for e in elsewhere:
                counts[universe_of(e)] = counts.get(universe_of(e), 0) + 1
            others = " Other stock groups: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + "."
        entries = [e for e in entries if universe_of(e) == universe]
    search = [e for e in entries if e.get("stage") == SEARCH]
    if not search:
        return "Your lab log for these stocks is empty: this is your first session on them." + others
    hidden = [e for e in entries if e.get("stage") in (HIDDEN_A, HIDDEN_B)]
    top = sorted(search, key=lambda e: -(e["result"]["annual_pct"] - e["result"]["spx_annual_pct"]))[:best]
    latest = search[-recent:]
    parts = [f"Your lab so far on these stocks: {len(search)} recipes searched, {len(hidden)} "
             f"hidden-years runs.{others}"]
    if hidden:
        parts += ["Hidden-years runs (each recipe runs there once, ever):"]
        parts += [f"- {e['stage']} [{e['key']}] {e['verdict']}" for e in hidden]
    parts += ["Your best searches by excess over the index:", *(f"- {_line(e)}" for e in top)]
    parts += ["Your latest searches:", *(f"- {_line(e)}" for e in latest if e not in top)]
    return "\n".join(parts)
