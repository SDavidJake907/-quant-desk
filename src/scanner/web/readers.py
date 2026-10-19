"""Read-only views of the repo's own files, shaped for the dashboard.

Every function here only reads. The official numbers come from the files the
scheduled jobs already write (logs/competition/*.md, data/competition/
snapshots.jsonl, logs/trading_audit.jsonl); nothing is recomputed, so the page
can never disagree with scripts/competition.py.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from scanner.execution import ownership

#: Display name in standings.md -> stable key the frontend colours by.
STANDINGS_KEYS = {
    "You (picks)": "you",
    "Gemma": "gemma",
    "Ghost-you (random 8)": "ghost_you",
    "Ghost-Gemma (random 5)": "ghost_gemma",
    "Momentum rule": "momentum",
    "S&P 500": "spx",
}

#: Audit events worth showing in the activity feed (orders, exits, refusals,
#: protection). Ratings, reviews and slates are left to the logs.
FEED_EVENTS = frozenset({
    "order_submitted", "trade_outcome", "picks_exit", "picks_refused",
    "picks_entry_cancelled", "gemma_decision", "gemma_exit", "momentum_order",
    "momentum_exit", "candidate_refused", "position_unprotected",
    "protection_restored", "weekend_flatten",
})

#: client_order_ids written by the test suite before it was isolated (10-15..10-17).
_TEST_COID = re.compile(r"^smc-TEST-")

_ROW = re.compile(r"^\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<ret>[+-]?\d+(?:\.\d+)?)%\s*\|$")
_MONTH = re.compile(r"^## (?P<month>\d{4}-\d{2})(?P<live> \(in progress\))?")
_PROB = re.compile(r"Gemma is really the better trader so far: (?P<g>\d+)% \(you: (?P<y>\d+)%\)")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def modified_at(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except FileNotFoundError:
        return None


# ---- competition ------------------------------------------------------------

def parse_standings(text: str) -> dict[str, Any]:
    """The latest month's table, head-to-head line, quarter tally and the
    resampled probability, from logs/competition/standings.md."""
    months: list[dict[str, Any]] = []
    quarters: list[str] = []
    section = ""
    probability: dict[str, float] | None = None
    probability_note = ""
    for raw in text.splitlines():
        line = raw.strip()
        m = _MONTH.match(line)
        if m:
            months.append({"month": m["month"], "in_progress": bool(m["live"]),
                           "rows": [], "verdict": ""})
            section = "month"
            continue
        if line.startswith("## Quarters"):
            section = "quarters"
            continue
        if line.startswith("## Season"):
            section = "season"
            continue
        if section == "month" and months:
            row = _ROW.match(line)
            if row and row["name"] in STANDINGS_KEYS:
                months[-1]["rows"].append({
                    "key": STANDINGS_KEYS[row["name"]], "name": row["name"],
                    "return_pct": float(row["ret"]),
                })
            elif line.startswith("**Head to head"):
                months[-1]["verdict"] = line.replace("**", "")
        elif section == "quarters" and line.startswith("- "):
            quarters.append(line[2:])
        elif section == "season":
            p = _PROB.search(line)
            if p:
                probability = {"gemma": int(p["g"]) / 100, "you": int(p["y"]) / 100}
            elif line.startswith("How sure is the lead?"):
                probability_note = line
    return {
        "latest": months[-1] if months else None,
        "months": months,
        "quarters": quarters,
        "probability": probability,
        "probability_note": probability_note,
    }


def parse_selection(text: str) -> list[dict[str, Any]]:
    """[{horizon, lines}] from logs/competition/selection.md."""
    out: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            out.append({"horizon": line[3:], "lines": []})
        elif line.startswith("- ") and out:
            out[-1]["lines"].append(line[2:].replace("**", ""))
    return out


def pl_series(snapshots: Path) -> dict[str, list[dict[str, Any]]]:
    """Each live book's total P/L in dollars by day. A re-run on the same day
    replaces the earlier line, as in scripts/competition.py."""
    last: dict[tuple[str, str], float] = {}
    for raw in read_text(snapshots).splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        book, day = row.get("book"), row.get("day")
        if book in ("picks", "gemma") and day:
            last[(book, day)] = float(row.get("pl_total") or 0.0)
    series: dict[str, list[dict[str, Any]]] = {"picks": [], "gemma": []}
    for (book, day), pl in sorted(last.items(), key=lambda kv: kv[0][1]):
        series[book].append({"day": day, "pl": round(pl, 2)})
    return series


# ---- system -----------------------------------------------------------------

def freeze_header(text: str) -> dict[str, str]:
    """Window number, policy fingerprint and build from the top of FREEZE.md."""
    out: dict[str, str] = {}
    for key, pattern in (("window", r"^\s*window\s+(\S+)"),
                         ("fingerprint", r"^\s*policy fingerprint\s+(\S+)"),
                         ("build", r"^\s*code build\s+(\S+)")):
        m = re.search(pattern, text, re.MULTILINE)
        if m:
            out[key] = m.group(1)
    return out


def schedule_health(logs_dir: Path) -> list[dict[str, Any]]:
    """When each scheduled job last wrote its log, newest first."""
    jobs = []
    for log in logs_dir.glob("scheduled_*.log"):
        jobs.append({"task": log.stem.removeprefix("scheduled_"),
                     "last_run": modified_at(log)})
    return sorted(jobs, key=lambda j: j["last_run"] or "", reverse=True)


def watchlist_symbols(path: Path) -> list[str]:
    """Tickers in a watchlist file, comments and blanks dropped."""
    out = []
    for raw in read_text(path).splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line.upper())
    return out


# ---- audit ------------------------------------------------------------------

def _coid(row: Mapping[str, Any]) -> str:
    intent = row.get("intent") or {}
    outcome = row.get("outcome") or {}
    return str(intent.get("client_order_id") or outcome.get("client_order_id")
               or row.get("client_order_id") or row.get("order_id") or "")


def _summary(row: Mapping[str, Any]) -> tuple[str, float | None]:
    """A one-line description of the event, and its realised P/L if closed."""
    event = row.get("event")
    if event == "trade_outcome":
        out = row.get("outcome") or {}
        dollars = out.get("realized_dollars")
        r = out.get("realized_r")
        parts = []
        if dollars is not None:
            parts.append(f"{'+' if dollars >= 0 else '-'}${abs(dollars):,.0f}")
        if r is not None:
            parts.append(f"{r:+.2f}R")
        if out.get("exit_reason"):
            parts.append(str(out["exit_reason"]))
        return " · ".join(parts), (float(dollars) if dollars is not None else None)
    if event == "gemma_decision":
        try:
            plan = json.loads(row.get("reason") or "")
        except ValueError:
            plan = None
        if isinstance(plan, dict):
            parts = [f"{label} {', '.join(plan.get(key) or []) or '—'}"
                     for key, label in (("keep", "keep"), ("buys", "buy"), ("sells", "sell"))]
            return " · ".join(parts), None
    return str(row.get("reason") or "")[:160], None


def activity(audit_log: Path, limit: int = 40) -> list[dict[str, Any]]:
    """The newest trading events, with the book each belongs to."""
    rows: list[dict[str, Any]] = []
    for raw in read_text(audit_log).splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if row.get("event") not in FEED_EVENTS:
            continue
        coid = _coid(row)
        if _TEST_COID.match(coid):
            continue
        intent = row.get("intent") or {}
        summary, realized = _summary(row)
        rows.append({
            "at": row.get("at"),
            "event": row.get("event"),
            "symbol": row.get("symbol") or intent.get("symbol") or "",
            "book": _book_of(coid, row.get("event", "")),
            "reason": summary,
            "realized": realized,
        })
    return list(reversed(rows))[:limit]


def _book_of(coid: str, event: str) -> str:
    strategy = ownership.strategy_of(coid)
    if strategy != ownership.UNKNOWN:
        return strategy
    for book in ("picks", "gemma", "momentum"):
        if event.startswith(book):
            return book
    return ""


# ---- broker -----------------------------------------------------------------

def _stops_by_symbol(open_orders: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    stops: dict[str, float] = {}
    for order in open_orders:
        for o in (order, *(order.get("legs") or ())):
            if o.get("side") == "sell" and o.get("stop_price"):
                stops[str(o.get("symbol"))] = float(o["stop_price"])
    return stops


def group_positions(positions: Iterable[Mapping[str, Any]],
                    all_orders: Iterable[Mapping[str, Any]],
                    open_orders: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Open positions by book, with the live stop where one is resting.

    Picks and one-off manual trades share the operator's half, so both land
    under "picks"; anything the system cannot attribute goes to "other".
    """
    owner_map = ownership.owners(all_orders)
    stops = _stops_by_symbol(open_orders)
    books: dict[str, list[dict[str, Any]]] = {"picks": [], "gemma": [], "other": []}
    for p in positions:
        symbol = str(p.get("symbol") or "")
        strategy = ownership.owner(symbol, owner_map)
        book = {ownership.PICKS: "picks", ownership.MANUAL: "picks",
                ownership.GEMMA: "gemma"}.get(strategy, "other")
        books[book].append({
            "symbol": symbol,
            "strategy": strategy,
            "qty": float(p.get("qty") or 0),
            "avg_entry": float(p.get("avg_entry_price") or 0),
            "price": float(p.get("current_price") or 0),
            "market_value": float(p.get("market_value") or 0),
            "pl": float(p.get("unrealized_pl") or 0),
            "pl_pct": float(p.get("unrealized_plpc") or 0) * 100,
            "stop": stops.get(symbol),
        })
    for rows in books.values():
        rows.sort(key=lambda r: r["market_value"], reverse=True)
    return books
