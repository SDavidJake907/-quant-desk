"""Market context for Gemma's morning facts (WORKLIST §48). Pure formatting.

What Gemma asked for on 2026-10-07 ("the macro tide") that can be fetched
daily and reliably from the price source the desk already uses. Every line is
CONTEXT: none of these was a tested edge here (breadth §25, VIX bands §17), and
the prompt says so.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence


def percentile(value: float, history: Sequence[float]) -> float:
    """Share of `history` at or below `value`, in %."""
    if not history:
        return 50.0
    return sum(1 for h in history if h <= value) / len(history) * 100


def vix_text(closes: Sequence[float]) -> str:
    if not closes:
        return "VIX unavailable"
    last = closes[-1]
    pct = percentile(last, closes[-252:])
    mood = "calm" if pct < 33 else "elevated" if pct > 67 else "middling"
    return f"VIX {last:.1f} ({mood}: higher than {pct:.0f}% of the past year)"


def breadth(series: Iterable[Sequence[float]], window: int = 50) -> float | None:
    """% of stocks closing above their own `window`-day average."""
    flags = [s[-1] > sum(s[-window:]) / window for s in series if len(s) >= window]
    return sum(flags) / len(flags) * 100 if flags else None


def yield_text(closes: Sequence[float]) -> str:
    """^TNX quotes the 10-year yield in percent."""
    if len(closes) < 22:
        return "10-year Treasury yield unavailable"
    change = closes[-1] - closes[-22]
    return f"10-year Treasury yield {closes[-1]:.2f}% ({change:+.2f} pts over a month)"


def events_text(events: Mapping[str, Iterable[str]]) -> str:
    """{'Tue 10/13': ['CPI', ...]} -> one line; quiet days are skipped."""
    parts = [f"{day} {', '.join(sorted(set(ev)))}" for day, ev in events.items() if ev]
    return "Coming market events: " + ("; ".join(parts) if parts else "none in the next five sessions")


def decisions_text(decisions: Sequence[Mapping]) -> str:
    """Gemma's last few submitted decisions, newest last."""
    if not decisions:
        return "Your recent decisions: none yet."
    parts = []
    for d in decisions:
        moves = []
        if d.get("buys"):
            moves.append("bought " + ", ".join(d["buys"]))
        if d.get("sells"):
            moves.append("sold " + ", ".join(d["sells"]))
        parts.append(f"{d['day']}: {'; '.join(moves) or 'held'}")
    return "Your recent decisions: " + " | ".join(parts) + "."
