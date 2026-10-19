"""Gemma's portfolio review, and the operator's questions to her.

Operator 2026-10-19: "a full view of the portfolio, and postion, and anwser,
with the next stocks coming up" -- and "we need some task, question anwsers".

THE REVIEW (scripts/gemma_review.py, after the close): her book's numbers are
computed here (money at work, weight, room to the stop); she writes a verdict
per holding (keep / watch / sell candidate), a summary, a watchlist of the next
stocks from her pool shortlist with what would make her buy, the coming events
that matter, and up to PROPOSALS rule changes she would ask the operator for.
It trades nothing: verdicts are notes, the 05:45 run still decides.

ASK GEMMA (dashboard): a question, answered in plain words from the same
facts -- her morning briefing, her latest review, recent questions.

PURE: numbers and text in, text and checked answers out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from scanner.models import Frozen

VERDICTS = ("keep", "watch", "sell_candidate")
WATCHLIST = 8
PROPOSALS = 3
ASK_TOKENS = 900
MAX_QUESTION_CHARS = 500
MAX_TEXT_CHARS = 600
HISTORY = 4


class PositionView(Frozen):
    symbol: str
    qty: float
    entry: float
    price: float
    value: float
    pl_dollars: float
    pl_pct: float
    weight_pct: float
    stop: float | None = None
    stop_gap_pct: float | None = None


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def position_views(positions: Iterable[Mapping[str, Any]], stops: Mapping[str, float],
                   *, sleeve: float) -> list[PositionView]:
    """One row per holding. `weight_pct` is of her whole book (the sleeve)."""
    out = []
    for p in positions:
        s = str(p.get("symbol", ""))
        price, value = _f(p.get("current_price")), _f(p.get("market_value"))
        stop = stops.get(s)
        out.append(PositionView(
            symbol=s, qty=_f(p.get("qty")), entry=_f(p.get("avg_entry_price")), price=price,
            value=value, pl_dollars=_f(p.get("unrealized_pl")),
            pl_pct=_f(p.get("unrealized_plpc")) * 100,
            weight_pct=value / sleeve * 100 if sleeve > 0 else 0.0,
            stop=stop, stop_gap_pct=(price - stop) / price * 100 if stop and price else None))
    return out


def book_numbers(views: Sequence[PositionView], *, sleeve: float) -> dict[str, float]:
    invested = sum(v.value for v in views)
    return {"sleeve": sleeve, "invested": invested,
            "invested_pct": invested / sleeve * 100 if sleeve > 0 else 0.0,
            "idle": max(0.0, sleeve - invested), "positions": len(views),
            "open_pl": sum(v.pl_dollars for v in views)}


def _view_line(v: PositionView) -> str:
    stop = (f"stop {v.stop:,.2f} ({v.stop_gap_pct:.1f}% below)" if v.stop is not None
            and v.stop_gap_pct is not None else "no stop found")
    return (f"- {v.symbol}: {v.pl_pct:+.1f}% (${v.pl_dollars:+,.0f}), ${v.value:,.0f} = "
            f"{v.weight_pct:.0f}% of your book, {stop}")


def prompt(facts: Mapping[str, str], views: Sequence[PositionView], *,
           numbers: Mapping[str, float], scoreboard: str, market: str,
           candidates: Sequence[str]) -> str:
    held = [v.symbol for v in views]
    return "\n".join((
        "You are the desk's junior trader. The market has closed. Write your EVENING "
        "REVIEW of your own book for the operator. This review trades nothing: your "
        "05:45 decision tomorrow still decides. Be honest and specific.",
        "",
        f"Your scoreboard: {scoreboard or 'not available'}",
        f"Market: {market}",
        "",
        f"Your book: ${numbers['invested']:,.0f} of ${numbers['sleeve']:,.0f} invested "
        f"({numbers['invested_pct']:.0f}%), ${numbers['idle']:,.0f} idle, "
        f"{int(numbers['positions'])} positions, open P/L ${numbers['open_pl']:+,.0f}.",
        *(_view_line(v) for v in views),
        "",
        "Your holdings, in full:",
        *(f"[{s}]\n{facts[s]}\n" for s in held if s in facts),
        "Candidates from your stock pool for the coming days:",
        *(f"[{s}]\n{facts[s]}\n" for s in candidates if s in facts),
        "Write:",
        "- summary: how the book stands and what matters most tomorrow, 2-4 sentences.",
        "- positions: for EACH holding a verdict -- keep (reason intact), watch (something "
        "to check) or sell_candidate (the reason has failed) -- and why, quoting its block "
        "where you can, plus the main risk (earnings date, event, stop distance).",
        f"- watchlist: up to {WATCHLIST} candidates you would most like to own next, why, "
        "and buy_if: what you would need to see to buy it.",
        "- upcoming: the dates and events in the next week that could move your book.",
        f"- proposals: up to {PROPOSALS} changes to YOUR rules you would ask the operator "
        "for (money, size, stops, slots, what you are shown), each with why. Leave it "
        "empty if you need nothing.",
    ))


def _text() -> dict[str, Any]:
    return {"type": "string"}


def schema(held: Sequence[str], candidates: Sequence[str]) -> dict[str, Any]:
    held_item = {"type": "object", "properties": {
        "symbol": {"type": "string", "enum": list(held) or [""]},
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "reason": _text(), "risk": _text()}, "required": ["symbol", "verdict", "reason", "risk"]}
    watch_item = {"type": "object", "properties": {
        "symbol": {"type": "string", "enum": list(candidates) or [""]},
        "why": _text(), "buy_if": _text()}, "required": ["symbol", "why", "buy_if"]}
    proposal = {"type": "object", "properties": {"change": _text(), "why": _text()},
                "required": ["change", "why"]}
    return {"type": "object", "properties": {
        "summary": _text(),
        "positions": {"type": "array", "maxItems": max(1, len(held)), "items": held_item},
        "watchlist": {"type": "array", "maxItems": WATCHLIST, "items": watch_item},
        "upcoming": {"type": "array", "maxItems": 8, "items": _text()},
        "proposals": {"type": "array", "maxItems": PROPOSALS, "items": proposal},
    }, "required": ["summary", "positions", "watchlist", "upcoming", "proposals"]}


def _s(x: Any) -> str:
    return str(x or "").strip()[:MAX_TEXT_CHARS]


def _items(answer: Any, key: str) -> list:
    got = answer.get(key) if isinstance(answer, dict) else None
    return [i for i in got if isinstance(i, dict)] if isinstance(got, list) else []


def validate(answer: Any, *, held: Sequence[str], candidates: Sequence[str]) -> dict[str, Any]:
    """Her review, checked. Never raises: every holding gets a row even when she
    skipped it, and an unusable answer is a review that says so."""
    ok = isinstance(answer, dict)
    first: dict[str, dict] = {}
    for p in _items(answer, "positions"):
        s = _s(p.get("symbol")).upper()
        if s in held and s not in first:
            verdict = p.get("verdict") if p.get("verdict") in VERDICTS else "keep"
            first[s] = {"symbol": s, "verdict": verdict, "reason": _s(p.get("reason")),
                        "risk": _s(p.get("risk"))}
    positions = [first.get(s) or {"symbol": s, "verdict": "keep",
                                  "reason": "(no review from Gemma)", "risk": ""}
                 for s in held]
    allowed, seen, watchlist = set(candidates) - set(held), set(), []
    for w in _items(answer, "watchlist"):
        s = _s(w.get("symbol")).upper()
        if s in allowed and s not in seen:
            seen.add(s)
            watchlist.append({"symbol": s, "why": _s(w.get("why")), "buy_if": _s(w.get("buy_if"))})
    upcoming = answer.get("upcoming") if ok and isinstance(answer.get("upcoming"), list) else []
    return {
        "summary": _s(answer.get("summary")) if ok else "(review unavailable: Gemma gave no usable answer)",
        "positions": positions,
        "watchlist": watchlist[:WATCHLIST],
        "upcoming": [_s(u) for u in upcoming if _s(u)][:8],
        "proposals": [{"change": _s(p.get("change")), "why": _s(p.get("why"))}
                      for p in _items(answer, "proposals") if _s(p.get("change"))][:PROPOSALS],
    }


def review_text(review: Mapping[str, Any] | None) -> str:
    """A saved review as compact text: the context for her answers."""
    if not review:
        return "(no review yet)"
    n = review.get("numbers") or {}
    lines = [f"Your review of {review.get('day', '?')}: {review.get('summary', '')}"]
    if n:
        lines.append(f"Book: ${n.get('invested', 0):,.0f} invested "
                     f"({n.get('invested_pct', 0):.0f}%), {int(n.get('positions', 0))} positions.")
    lines += [f"- {p['symbol']}: {p['verdict']} -- {p.get('reason', '')}"
              for p in review.get("positions") or []]
    lines += [f"Watchlist: {w['symbol']} -- {w.get('why', '')} (buy if {w.get('buy_if', '')})"
              for w in review.get("watchlist") or []]
    lines += [f"Proposal: {p['change']} -- {p.get('why', '')}" for p in review.get("proposals") or []]
    return "\n".join(lines)


def ask_prompt(question: str, *, briefing: str, review: str,
               history: Sequence[tuple[str, str]] = ()) -> str:
    recent = [f"Q: {q}\nA: {a}" for q, a in list(history)[-HISTORY:]]
    return "\n".join((
        "You are the desk's junior trader. The operator is asking you a question about "
        "your book. Answer in plain words, directly and honestly, using only the facts "
        "below; say so when they do not tell you. Do not invent prices or numbers. "
        "Keep it under 250 words.",
        "",
        "=== This morning's briefing (what you traded on) ===",
        briefing or "(not available)",
        "",
        "=== Your latest evening review ===",
        review,
        *(("", "=== Recent questions ===", *recent) if recent else ()),
        "",
        f"Question: {question}",
    ))


def clean_question(text: Any) -> str | None:
    """A question as asked, or None when empty or too long."""
    q = str(text or "").strip()
    return q if q and len(q) <= MAX_QUESTION_CHARS else None
