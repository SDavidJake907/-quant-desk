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
from datetime import date, time, timedelta

from scanner.advisor.news_impact import Impact
from scanner.models import Frozen
from scanner.sources.calendar import Earnings
from scanner.sources.market_calendar import Session

#: Dated events no feed carries. FOMC dates from federalreserve.gov
#: (fomccalendars.htm, read 2026-10-08); add 2027 when the Fed publishes it.
FIXED_EVENTS: tuple[tuple[date, str, str], ...] = (
    (date(2026, 10, 27), "fed", "FOMC meeting starts (Oct 27-28, decision on the 28th)"),
    (date(2026, 10, 28), "fed", "FOMC rate decision"),
    (date(2026, 11, 3), "political", "US midterm elections"),
    (date(2026, 12, 8), "fed", "FOMC meeting starts (Dec 8-9, decision on the 9th)"),
    (date(2026, 12, 9), "fed", "FOMC rate decision"),
)
#: Earnings that move a whole sector even when no book holds them (WORKLIST
#: §54). Sector names as in the index constituents; TSM and ASML are not S&P
#: members, so their sector is given here.
BELLWETHERS: dict[str, tuple[str, ...]] = {
    "NVDA": ("Information Technology",), "TSM": ("Information Technology",),
    "ASML": ("Information Technology",), "AVGO": ("Information Technology",),
    "MSFT": ("Information Technology",), "AAPL": ("Information Technology",),
    "GOOGL": ("Communication Services",), "META": ("Communication Services",),
    "AMZN": ("Consumer Discretionary",), "TSLA": ("Consumer Discretionary",),
    "JPM": ("Financials",), "XOM": ("Energy",), "CVX": ("Energy",),
    "UNH": ("Health Care",), "LLY": ("Health Care",), "CAT": ("Industrials",),
}
#: Kinds that touch every position, not one stock.
MARKET_WIDE = ("econ", "fed", "political", "market")
#: Sector and bellwether calendar lines per stock in Gemma's facts.
MAX_SECTOR_LINES = 3
#: News lines per stock in Gemma's facts: enough to inform, not to crowd.
MAX_NEWS_LINES = 3
CONTEXT_NOTE = ("Headlines and scheduled events are usually already in the price within minutes: "
                "use them for timing and risk (earnings, CPI, the Fed), not as a reason to swap.")


class Event(Frozen):
    day: date
    kind: str           # econ | earnings | fed | political | market | sector | bellwether
    text: str
    symbols: tuple[str, ...] = ()
    when: str = ""      # earnings only: pre-mkt | after-close | ""
    sectors: tuple[str, ...] = ()     # sector and bellwether events


def _label(day: date) -> str:
    return f"{day:%a %m/%d}"


def _third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def market_mechanics(start: date, end: date) -> list[tuple[date, str, str]]:
    """Options expiry (third Friday) and, in Mar/Jun/Sep/Dec, quad witching and
    the S&P quarterly rebalance. Computed from the rule, never typed in. (A
    third-Friday holiday moves expiry to Thursday; not modelled -- rare.)"""
    out, y, m = [], start.year, start.month
    while date(y, m, 1) <= end:
        day = _third_friday(y, m)
        if start <= day <= end:
            text = ("quad witching (stock and index options and futures expire) "
                    "and S&P quarterly rebalance" if m in (3, 6, 9, 12)
                    else "monthly options expiry")
            out.append((day, "market", text))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def closures(sessions: Iterable[Session], start: date, end: date) -> list[tuple[date, str, str]]:
    """Holidays (weekdays with no session) and early closes, from the broker."""
    by_day = {s.on: s for s in sessions}
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            s = by_day.get(day)
            if s is None:
                out.append((day, "market", "market CLOSED (holiday)"))
            elif s.closes < time(16, 0):
                out.append((day, "market", f"early close {s.closes:%H:%M} ET"))
        day += timedelta(days=1)
    return out


def events(days: Sequence[date], *, econ: Mapping[date, Iterable[str]],
           earnings: Mapping[str, Earnings],
           fixed: Iterable[tuple[date, str, str]] = FIXED_EVENTS,
           sector_econ: Mapping[date, Iterable[tuple[str, tuple[str, ...]]]] | None = None,
           bellwethers: Mapping[str, Earnings] | None = None,
           bellwether_sectors: Mapping[str, tuple[str, ...]] = BELLWETHERS) -> list[Event]:
    """Everything dated inside `days`, in date order."""
    window = set(days)
    out = [Event(day=d, kind="econ", text=name) for d, names in econ.items() if d in window
           for name in names]
    out += [Event(day=d, kind="sector", text=name, sectors=sectors)
            for d, items in (sector_econ or {}).items() if d in window
            for name, sectors in items]
    out += [Event(day=e.on, kind="bellwether", symbols=(s,), when=e.when,
                  sectors=bellwether_sectors.get(s, ()),
                  text=f"{s} reports earnings" + (f" {e.when}" if e.when else ""))
            for s, e in (bellwethers or {}).items() if e.on in window and s not in earnings]
    out += [Event(day=e.on, kind="earnings", symbols=(s,), when=e.when,
                  text=f"{s} reports earnings" + (f" {e.when}" if e.when else ""))
            for s, e in earnings.items() if e.on in window]
    out += [Event(day=d, kind=k, text=t) for d, k, t in fixed if d in window]
    return sorted(out, key=lambda e: (e.day, e.kind != "fed", e.kind, e.text))


def flags(evts: Iterable[Event], *, held: Mapping[str, str],
          sectors: Mapping[str, str] | None = None) -> list[str]:
    """Warnings for what the books hold: `held` maps symbol -> book, `sectors`
    symbol -> sector (for sector and bellwether events)."""
    sectors = sectors or {}
    out = []
    for e in evts:
        if e.kind == "earnings" and e.symbols and e.symbols[0] in held:
            s = e.symbols[0]
            out.append(f"{s} ({held[s]}) reports earnings {_label(e.day)}: a gap can jump its stop")
        elif e.kind in ("sector", "bellwether"):
            touched = sorted(s for s in held if sectors.get(s) in e.sectors and s not in e.symbols)
            if touched:
                why = "bellwether earnings" if e.kind == "bellwether" else "sector release"
                out.append(f"{_label(e.day)} {e.text} ({why}): touches {', '.join(touched)}")
        elif e.kind in MARKET_WIDE and held:
            out.append(f"{_label(e.day)} {e.text}: market-wide, touches every position")
    return out


def stock_lines(symbol: str, evts: Iterable[Event], impacts: Iterable[Impact],
                sector: str = "") -> list[str]:
    """Lines for one stock's facts block: its own dates, its sector's dates
    (releases and bellwether earnings), and its own headlines."""
    evts = list(evts)
    lines = [f"calendar: reports earnings {_label(e.day)}" + (f" {e.when}" if e.when else "")
             for e in evts if e.kind in ("earnings", "bellwether") and symbol in e.symbols]
    if sector:
        lines += [f"calendar: {_label(e.day)} {e.text} "
                  + (f"(bellwether for {sector})" if e.kind == "bellwether" else f"({sector})")
                  for e in evts if e.kind in ("sector", "bellwether") and sector in e.sectors
                  and symbol not in e.symbols][:MAX_SECTOR_LINES]
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
