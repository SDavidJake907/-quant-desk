"""The daily brief: what is coming, what the news is, which stocks it touches
(WORKLIST §52). Written at 03:00 AK by scripts/week_ahead.py; read by Gemma at
05:45 (scripts/gemma_trade.py) and by the operator (logs/week_ahead/).

WHAT GEMMA GETS, AND WHAT IT DOES NOT
  - market line: the next sessions' market-wide events (CPI, Fed...), the
    President's market-relevant posts and today's schedule, top headlines;
  - each stock's own block: its earnings date if inside the window, and the
    headlines linked to it.
It gets the FACTS (dates, headlines), not its own 03:00 read of them, so it
is not anchored on an earlier opinion. Every line is labelled context: the
news is usually in the price within minutes (§49), so it is for timing and
risk, not a reason to swap.

PURE.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date

from scanner.advisor.news_impact import Impact
from scanner.models import Frozen
from scanner.sources.calendar import Earnings

#: Dated events no feed carries. FOMC dates from federalreserve.gov
#: (fomccalendars.htm, read 2026-10-08); add 2027 when the Fed publishes it.
FIXED_EVENTS: tuple[tuple[date, str, str], ...] = (
    (date(2026, 10, 27), "fed", "FOMC meeting starts (Oct 27-28, decision on the 28th)"),
    (date(2026, 10, 28), "fed", "FOMC rate decision"),
    (date(2026, 11, 3), "political", "US midterm elections"),
    (date(2026, 12, 8), "fed", "FOMC meeting starts (Dec 8-9, decision on the 9th)"),
    (date(2026, 12, 9), "fed", "FOMC rate decision"),
)
#: Kinds that touch every position, not one stock.
MARKET_WIDE = ("econ", "fed", "political")
#: News lines per stock in Gemma's facts: enough to inform, not to crowd.
MAX_NEWS_LINES = 3
CONTEXT_NOTE = ("Headlines and scheduled events are usually already in the price within minutes: "
                "use them for timing and risk (earnings, CPI, the Fed), not as a reason to swap.")


class Event(Frozen):
    day: date
    kind: str           # econ | earnings | fed | political
    text: str
    symbols: tuple[str, ...] = ()
    when: str = ""      # earnings only: pre-mkt | after-close | ""


def _label(day: date) -> str:
    return f"{day:%a %m/%d}"


def events(days: Sequence[date], *, econ: Mapping[date, Iterable[str]],
           earnings: Mapping[str, Earnings],
           fixed: Iterable[tuple[date, str, str]] = FIXED_EVENTS) -> list[Event]:
    """Everything dated inside `days`, in date order."""
    window = set(days)
    out = [Event(day=d, kind="econ", text=name) for d, names in econ.items() if d in window
           for name in names]
    out += [Event(day=e.on, kind="earnings", symbols=(s,), when=e.when,
                  text=f"{s} reports earnings" + (f" {e.when}" if e.when else ""))
            for s, e in earnings.items() if e.on in window]
    out += [Event(day=d, kind=k, text=t) for d, k, t in fixed if d in window]
    return sorted(out, key=lambda e: (e.day, e.kind != "fed", e.kind, e.text))


def flags(evts: Iterable[Event], *, held: Mapping[str, str]) -> list[str]:
    """Warnings for what the books hold: `held` maps symbol -> book."""
    out = []
    for e in evts:
        if e.kind == "earnings" and e.symbols and e.symbols[0] in held:
            s = e.symbols[0]
            out.append(f"{s} ({held[s]}) reports earnings {_label(e.day)}: a gap can jump its stop")
        elif e.kind in MARKET_WIDE and held:
            out.append(f"{_label(e.day)} {e.text}: market-wide, touches every position")
    return out


def stock_lines(symbol: str, evts: Iterable[Event], impacts: Iterable[Impact]) -> list[str]:
    """Lines for one stock's facts block: its own dates and its own headlines."""
    lines = [f"calendar: reports earnings {_label(e.day)}" + (f" {e.when}" if e.when else "")
             for e in evts if e.kind == "earnings" and symbol in e.symbols]
    lines += [f"in the news: {i.headline}" for i in impacts if symbol in i.symbols][:MAX_NEWS_LINES]
    return lines


def market_text(evts: Iterable[Event], *, president_lines: Sequence[str],
                headlines: Sequence[str], max_events: int = 8) -> str:
    """One paragraph for Gemma's market line. Short on purpose."""
    wide = [e for e in evts if e.kind in MARKET_WIDE][:max_events]
    parts = []
    if wide:
        parts.append("Coming up: " + "; ".join(f"{_label(e.day)}: {e.text}" for e in wide))
    if president_lines:
        parts.append("The President: " + " | ".join(p[:300] for p in president_lines[:3]))
    if headlines:
        parts.append("Top headlines: " + " | ".join(h[:160] for h in headlines[:6]))
    parts.append(CONTEXT_NOTE)
    return ". ".join(parts)


def render_md(day: date, evts: Sequence[Event], flag_lines: Sequence[str],
              posts_md: Sequence[str], schedule_md: Sequence[str],
              impacts: Sequence[Impact], unlinked: Sequence[str]) -> str:
    """The operator's copy (logs/week_ahead/YYYY-MM-DD.md)."""
    lines = [f"# Daily brief {day:%A %Y-%m-%d}", "",
             "What is coming, what the news is, which stocks it touches. Gemma reads the same "
             "facts at 05:45. " + CONTEXT_NOTE, "", "## Watch: your positions", ""]
    lines += [f"- ⚠ {f}" for f in flag_lines] or ["- nothing dated against a held position"]
    lines += ["", "## Calendar, next sessions", "", "| Day | What | Kind |", "|---|---|---|"]
    lines += [f"| {_label(e.day)} | {e.text} | {e.kind} |" for e in evts] or ["| - | nothing found | - |"]
    lines += ["", "## The President", "", "**Schedule (official, as published):**"]
    lines += [f"- {s}" for s in schedule_md] or ["- not published yet"]
    lines += ["", "**Posts and releases** _(Truth Social via the trumpstruth.org mirror; whitehouse.gov)_:"]
    lines += [f"- {p}" for p in posts_md] or ["- none / feeds unavailable"]
    lines += ["", "## News -> stocks _(Gemma's read: helps / hurts / mixed / unclear; not a forecast)_", ""]
    for i in impacts:
        who = ", ".join(i.symbols) or "—"
        lines.append(f"- **{who}** {('[' + ', '.join(i.sectors) + ']') if i.sectors else ''} "
                     f"_{i.effect}_: {i.headline} — {i.why}")
    if not impacts:
        lines.append("- no headline linked to the desk's stocks")
    lines += ["", "## Other headlines", ""] + [f"- {h}" for h in unlinked]
    return "\n".join(lines) + "\n"
