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
#: The desk's classic-strategy library (scripts/gemma_lab.py --baseline): counted, shown for reference.
BASELINE = "baseline"


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


def scoring_of(e: Mapping[str, Any]) -> int:
    """1 = idle cash earned nothing (before 2026-10-23 evening); 2 = idle cash in the index."""
    return int(e.get("scoring") or 1)


def summary(entries: list[Mapping[str, Any]], *, universe: str | None = None, best: int = 10,
            recent: int = 15, scoring: int | None = None) -> str:
    """Her log for the prompt: the desk's baselines, the hidden-years runs, her best searches,
    her latest -- for one stock group when `universe` is given (other groups are counted in one
    line), and only results scored like today's when `scoring` is given (older ones counted)."""
    others = ""
    if universe is not None:
        elsewhere = [e for e in entries if e.get("stage") == SEARCH and universe_of(e) != universe]
        if elsewhere:
            counts: dict[str, int] = {}
            for e in elsewhere:
                counts[universe_of(e)] = counts.get(universe_of(e), 0) + 1
            others = " Other stock groups: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + "."
        entries = [e for e in entries if universe_of(e) == universe]
    older = 0
    if scoring is not None:
        older = sum(1 for e in entries if e.get("stage") == SEARCH and scoring_of(e) != scoring)
        entries = [e for e in entries if scoring_of(e) == scoring or e.get("stage") in (HIDDEN_A, HIDDEN_B)]
        if older:
            others += (f" {older} earlier searches here were scored with idle cash earning nothing -- not "
                       "comparable, not listed, still counted.")
    search = [e for e in entries if e.get("stage") == SEARCH]
    baselines = [e for e in entries if e.get("stage") == BASELINE]
    hidden = [e for e in entries if e.get("stage") in (HIDDEN_A, HIDDEN_B)]
    parts = [f"Your lab so far on these stocks: {len(search)} recipes searched, {len(hidden)} "
             f"hidden-years runs.{others}"]
    if baselines:
        parts += ["Desk baselines -- classic strategies run under the same rules, for reference:"]
        parts += [f"- {e.get('name', '')}: {_line(e)}" for e in baselines]
    if hidden:
        parts += ["Hidden-years runs (each recipe runs there once, ever):"]
        parts += [f"- {e['stage']} [{e['key']}] {e['verdict']}" for e in hidden]
    if not search:
        parts.append("No searches of your own on these stocks yet under today's scoring.")
        return "\n".join(parts)
    top = sorted(search, key=lambda e: -(e["result"]["annual_pct"] - e["result"]["spx_annual_pct"]))[:best]
    latest = search[-recent:]
    parts += ["Your best searches by excess over the index:", *(f"- {_line(e)}" for e in top)]
    parts += ["Your latest searches:", *(f"- {_line(e)}" for e in latest if e not in top)]
    return "\n".join(parts)
