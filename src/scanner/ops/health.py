"""The morning health check (operator 2026-10-20: "lets monitor all on schedule").

Deterministic, like the evening desk check (ops/deskcheck.py), and read-only:
it sends no order and changes nothing. Each check returns a Finding --
ok, watch (worth knowing) or problem (something broke or is unprotected).

  stops     every share held has a live sell stop. Bracket stop legs sit as
            "held" and are missing from status=open, so the caller passes
            status=all orders, nested (memory: alpaca-held-stop-legs).
  tasks     every enabled stock-scanner task's last run succeeded
  ollama    no runner process outlived its Ollama server (orphans have held
            50+ GB of commit and the GPU before)
  outputs   this morning's Hindsight lessons, Gemma's submitted decision,
            the last session's evening review

PURE.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path

from scanner.ops.deskcheck import Finding

LIVE = frozenset({"new", "held", "accepted", "pending_new", "partially_filled"})
STOP_TYPES = frozenset({"stop", "stop_limit", "trailing_stop"})
#: Task Scheduler results that are not failures: running, has not run yet, queued.
TASK_OK = frozenset({0, 0x41301, 0x41303, 0x41325})


def _legs(orders: Iterable[Mapping]) -> Iterable[Mapping]:
    for o in orders:
        yield o
        yield from o.get("legs") or ()


def stop_gaps(positions: Iterable[Mapping], orders: Iterable[Mapping]) -> list[Finding]:
    covered: dict[str, float] = defaultdict(float)
    for leg in _legs(orders):
        if leg.get("side") == "sell" and leg.get("type") in STOP_TYPES and leg.get("status") in LIVE:
            covered[leg["symbol"]] += float(leg.get("qty") or 0)
    found, n = [], 0
    for p in positions:
        n += 1
        s, qty = p["symbol"], abs(float(p["qty"]))
        have = covered.get(s, 0.0)
        if have <= 0:
            found.append(Finding(level="problem", text=f"{s}: {qty:g} shares and no live stop"))
        elif have < qty:
            found.append(Finding(level="problem", text=f"{s}: stops cover {have:g} of {qty:g} shares"))
    return found or [Finding(level="ok", text=f"stops: all {n} positions covered")]


def task_problems(tasks: Iterable[Mapping]) -> list[Finding]:
    found, n = [], 0
    for t in tasks:
        if t.get("state") == "Disabled":
            continue
        n += 1
        result = int(t.get("result") or 0)
        if result not in TASK_OK:
            short = t["name"].removeprefix("stock-scanner-")
            found.append(Finding(level="problem",
                                 text=f"task {short}: last run failed (0x{result:x}, {t.get('last') or '?'})"))
    return found or [Finding(level="ok", text=f"tasks: all {n} enabled tasks' last runs OK")]


def _is_runner(p: Mapping) -> bool:
    name = (p.get("name") or "").lower()
    return name == "llama-server.exe" or (name == "ollama.exe" and " runner" in (p.get("cmd") or "").lower())


def orphan_runners(procs: Iterable[Mapping]) -> list[Finding]:
    procs = list(procs)
    servers = {p["pid"] for p in procs if (p.get("name") or "").lower() == "ollama.exe" and not _is_runner(p)}
    found = []
    for p in procs:
        if not _is_runner(p):
            continue
        if p.get("ppid") in servers:
            found.append(Finding(level="watch", text=f"ollama: a model is loaded (pid {p['pid']})"))
        else:
            found.append(Finding(level="problem",
                                 text=f"ollama: orphan runner pid {p['pid']} ({p.get('name')}) -- "
                                      f"holds memory and the GPU; end it"))
    return found or [Finding(level="ok", text="ollama: no model loaded, no orphans")]


def lessons_finding(lessons: Mapping | None, *, now: datetime) -> Finding:
    try:
        at = datetime.fromisoformat(lessons["at"])           # type: ignore[index]
    except (TypeError, KeyError, ValueError):
        return Finding(level="problem", text="hindsight: no readable lessons file")
    if at.astimezone(now.tzinfo).date() != now.date():
        age = (now - at).total_seconds() / 3600
        return Finding(level="problem",
                       text=f"hindsight: lessons are {age:.0f}h old -- this morning's run failed")
    return Finding(level="ok", text=f"hindsight: lessons written {at:%H:%M}, {len(lessons.get('text', ''))} chars")


def decision_finding(rows: Iterable[Mapping], today: date) -> Finding:
    for r in reversed(list(rows)):
        if r.get("event") != "gemma_decision" or not str(r.get("at", "")).startswith(today.isoformat()):
            continue
        try:
            d = json.loads(r.get("reason") or "{}")
        except ValueError:
            continue
        if d.get("submitted"):
            return Finding(level="ok", text=f"gemma: decision submitted {str(r['at'])[11:16]}, "
                                            f"buys {d.get('buys') or []}, sells {d.get('sells') or []}")
    return Finding(level="problem", text="gemma: no submitted decision today")


def review_finding(folder: Path, session: date) -> Finding:
    if (folder / f"{session.isoformat()}.json").exists():
        return Finding(level="ok", text=f"review: {session} saved")
    return Finding(level="problem", text=f"review: no evening review saved for {session}")


def summary(findings: Iterable[Finding]) -> str:
    bad = [f.text for f in findings if f.level == "problem"]
    if not bad:
        return "ALL OK"
    return f"{len(bad)} problem{'s' if len(bad) > 1 else ''}: " + "; ".join(bad)


def render(findings: Iterable[Finding], *, now: datetime) -> str:
    findings = list(findings)
    bad = sum(f.level == "problem" for f in findings)
    head = "ALL OK" if not bad else f"{bad} PROBLEM{'S' if bad > 1 else ''}"
    order = {"problem": 0, "watch": 1, "ok": 2}
    lines = [f"health {now:%Y-%m-%d %H:%M}  {head}"]
    lines += [f"  [{f.level:<7}] {f.text}" for f in sorted(findings, key=lambda f: order[f.level])]
    return "\n".join(lines)
