"""Gemma's short memory: her own recent notes, each with what happened since.

Operator 2026-10-19: "Gemma having memory ... she would be able to have a short
memory." Every run starts blank (Ollama's cache only speeds a repeated prompt;
it remembers nothing between runs), so memory is a block in her prompt.

BUILT FROM HER RECORD AND PRICES, NOT FROM STORED TEXT
A text memory was tested on 2026-09-24 and made a model answer worse: it
recalled a deal ANNOUNCEMENT over its TERMINATION (llm/memory.py). So this
block holds only what she did and what it earned: her closed trades in R, her
last sessions (what she bought and sold, her journal line), her last review's
watchlist and verdicts -- and beside each note, the price change since she
wrote it, computed here. A note she wrote can go stale; the price change
cannot. The block says today's facts win.

PURE: rows, a review and a price lookup in; text out.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from scanner.models import Frozen

ENABLED = True
SESSIONS = 3
TRADES = 6
RECENT_STOP_DAYS = 14
JOURNAL_CHARS = 220
MAX_CHARS = 2400
PREFIX = "gem-"

ChangeSince = Callable[[str, str], float | None]


class Trade(Frozen):
    day: str
    symbol: str
    r: float
    dollars: float
    why: str


class Session(Frozen):
    day: str
    keep: tuple[str, ...]
    buys: tuple[str, ...]
    sells: tuple[str, ...]
    journal: str


def _body(row: Mapping[str, Any]) -> dict:
    try:
        body = json.loads(row.get("reason") or "{}")
    except (TypeError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def closed_trades(rows: Iterable[Mapping[str, Any]], limit: int = TRADES) -> list[Trade]:
    """Her closed trades (gem- order ids), one per order, newest first."""
    seen, out = set(), []
    for r in rows:
        o = r.get("outcome") or {}
        coid = str(o.get("client_order_id") or "")
        if r.get("event") != "trade_outcome" or not coid.startswith(PREFIX) or coid in seen:
            continue
        seen.add(coid)
        out.append(Trade(day=str(r.get("at", ""))[:10], symbol=str(o.get("symbol") or r.get("symbol")),
                         r=float(o.get("realized_r") or 0.0),
                         dollars=float(o.get("realized_dollars") or 0.0),
                         why=str(o.get("exit_reason") or "closed")))
    return sorted(out, key=lambda t: t.day, reverse=True)[:limit]


def sessions(rows: Iterable[Mapping[str, Any]], limit: int = SESSIONS) -> list[Session]:
    """Her submitted decisions (previews are not decisions), newest first, one per day."""
    by_day: dict[str, Session] = {}
    for r in rows:
        if r.get("event") != "gemma_decision":
            continue
        b = _body(r)
        if not b.get("submitted"):
            continue
        day = str(r.get("at", ""))[:10]
        by_day[day] = Session(day=day, keep=tuple(b.get("keep") or ()),
                              buys=tuple(b.get("buys") or ()), sells=tuple(b.get("sells") or ()),
                              journal=str(b.get("journal") or "").strip())
    return [by_day[d] for d in sorted(by_day, reverse=True)[:limit]]


def symbols_to_price(rows: Sequence[Mapping[str, Any]], *, review: Mapping | None) -> set[str]:
    """Names whose change since a note is shown: her recent sells and her watchlist."""
    names = {s for x in sessions(rows) for s in x.sells}
    if review:
        names |= {str(w.get("symbol")) for w in review.get("watchlist") or [] if w.get("symbol")}
    return names


def change_lookup(prices: Mapping[str, Mapping[str, float]]) -> ChangeSince:
    """prices[symbol] = {"YYYY-MM-DD": close, ..., "now": last}. Unknown -> None."""
    def since(symbol: str, day: str) -> float | None:
        p = prices.get(symbol) or {}
        then, now = p.get(day), p.get("now")
        return (now / then - 1) * 100 if then and now else None
    return since


def _since(change: float | None) -> str:
    return f" ({change:+.1f}% since)" if change is not None else ""


def block(rows: Sequence[Mapping[str, Any]], *, review: Mapping[str, Any] | None,
          change_since: ChangeSince, held: Iterable[str], today: date) -> str:
    held = set(held)
    trades, recent = closed_trades(rows), sessions(rows)
    lines = ["YOUR SHORT MEMORY -- your own recent record, with what happened since. Notes "
             "can go stale; today's facts below win when they disagree."]
    if not trades and not recent and not review:
        return "\n".join((*lines, "(no history yet)"))
    if trades:
        lines.append("Your closed trades: " + "; ".join(
            f"{t.symbol} {t.r:+.2f}R (${t.dollars:+,.0f}, {t.why}, {t.day})" for t in trades))
        cutoff = (today - timedelta(days=RECENT_STOP_DAYS)).isoformat()
        for t in trades:
            if t.why == "stop" and t.day >= cutoff and t.symbol in held:
                lines.append(f"Note: you hold {t.symbol} again; it stopped you out on {t.day} "
                             f"({t.r:+.2f}R). Be sure today's reason is new.")
    for s in recent:
        did = "; ".join(x for x in (
            f"bought {', '.join(s.buys)}" if s.buys else "",
            "; ".join(f"sold {x}{_since(change_since(x, s.day))}" for x in s.sells),
        ) if x) or f"held {', '.join(s.keep) or 'nothing'}"
        said = s.journal if len(s.journal) <= JOURNAL_CHARS else s.journal[:JOURNAL_CHARS].rstrip() + "..."
        note = f' You wrote: "{said}"' if s.journal else ""
        lines.append(f"- {s.day}: {did}.{note}")
    if review:
        day = str(review.get("day", ""))
        flagged = [p for p in review.get("positions") or [] if p.get("verdict") != "keep"]
        if flagged:
            lines.append(f"Your review of {day} flagged: " + "; ".join(
                f"{p['symbol']}: {p['verdict']} ({str(p.get('reason', ''))[:90]})" for p in flagged))
        watch = review.get("watchlist") or []
        if watch:
            lines.append(f"Your watchlist of {day}: " + "; ".join(
                f"{w['symbol']}{_since(change_since(w['symbol'], day))}, buy if "
                f"{str(w.get('buy_if', ''))[:70]}" for w in watch))
    text = "\n".join(lines)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS - 3] + "..."
