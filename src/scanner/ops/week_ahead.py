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
from scanner.sources.calendar import Earnings, Release, surprise
from scanner.sources.market_calendar import Session

#: Dated events no feed carries. FOMC dates from federalreserve.gov
#: (fomccalendars.htm, read 2026-10-08); add 2027 when the Fed publishes it.
FIXED_EVENTS: tuple[tuple[date, str, str], ...] = (
    (date(2026, 10, 27), "fed", "FOMC meeting starts (Oct 27-28, decision on the 28th)"),
    (date(2026, 10, 28), "fed", "FOMC rate decision"),
    (date(2026, 11, 3), "political", "US midterm elections"),
    (date(2026, 12, 8), "fed", "FOMC meeting starts (Dec 8-9, decision on the 9th)"),
    (date(2026, 12, 9), "fed", "FOMC rate decision"),
    # Index events from Nasdaq's Trading Calendar 2026 (nasdaq.com/trading-calendar,
    # read 2026-10-08). Options expiry, triple witching and holidays are computed
    # or come from the broker, and agree with it.
    (date(2026, 10, 30), "market", "Russell US index rank day (sets December membership)"),
    (date(2026, 11, 13), "market", "Russell preliminary add/delete lists, 6pm ET"),
    (date(2026, 11, 30), "market", "MSCI quarterly review takes effect: heavy trading at the close"),
    (date(2026, 12, 11), "market", "Nasdaq-100 additions and deletions announced"),
    (date(2026, 12, 18), "market", "Nasdaq-100 annual rebalance at the close"),
    # Big money movers no feed carries (WORKLIST §58), each from its own source,
    # read 2026-10-09: Treasury (refunding), the Fed (minutes, 3 weeks after the
    # meeting: expected), Congress's research service (the continuing resolution).
    (date(2026, 11, 4), "econ", "Treasury quarterly refunding announcement (how much debt it sells)"),
    (date(2026, 11, 18), "fed", "FOMC minutes of the Oct 27-28 meeting (expected)"),
    (date(2026, 12, 11), "political", "Government funding runs out (continuing resolution ends; "
                                      "shutdown risk from 12/12)"),
)
#: The operator's watch list (watchlists/watch.txt, WORKLIST §63): dated events
#: for watched names, kind "watch" -- shown in the operator's calendar only.
#: SPCX lockups as reported by IPO trackers 2026-10-14 (dates vary by source).
WATCH_EVENTS: tuple[tuple[date, str, str], ...] = (
    (date(2026, 11, 9), "watch", "SPCX: largest lockup tranche unlocks (~28% of locked shares)"),
    (date(2026, 12, 8), "watch", "SPCX: 180-day IPO lockup ends (remaining locked shares)"),
)

#: Dated events that move ONE part of the market (WORKLIST §58). OPEC's 4 Oct 2026
#: statement: "The next meeting will be held on 1 November 2026" (a Sunday, so the
#: first session after it); conference dates from the organisers.
FIXED_SECTOR_EVENTS: tuple[tuple[date, str, tuple[str, ...]], ...] = (
    (date(2026, 11, 2), "OPEC+ (7 countries) met Sun 11/01 on output", ("Energy",)),
    (date(2026, 11, 17), "Microsoft Ignite (Nov 17-20)", ("Information Technology",)),
    (date(2026, 11, 30), "AWS re:Invent (Nov 30-Dec 4) and NVIDIA GTC Washington (Nov 30-Dec 3)",
     ("Information Technology", "Communication Services")),
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
    expected: str = ""                # econ: the consensus, once published
    previous: str = ""                # econ: the last reading


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


def events(days: Sequence[date], *, econ: Mapping[date, Iterable[str | Release]],
           earnings: Mapping[str, Earnings],
           fixed: Iterable[tuple[date, str, str]] = FIXED_EVENTS,
           sector_econ: Mapping[date, Iterable[tuple[str, tuple[str, ...]]]] | None = None,
           bellwethers: Mapping[str, Earnings] | None = None,
           bellwether_sectors: Mapping[str, tuple[str, ...]] = BELLWETHERS,
           fixed_sector: Iterable[tuple[date, str, tuple[str, ...]]] = FIXED_SECTOR_EVENTS,
           ) -> list[Event]:
    """Everything dated inside `days`, in date order."""
    window = set(days)
    out = [Event(day=d, kind="econ", text=r) if isinstance(r, str) else
           Event(day=d, kind="econ", text=r.name, expected=r.consensus, previous=r.previous)
           for d, items in econ.items() if d in window for r in items]
    out += [Event(day=d, kind="sector", text=name, sectors=sectors)
            for d, items in (sector_econ or {}).items() if d in window
            for name, sectors in items]
    out += [Event(day=d, kind="sector", text=text, sectors=sectors)
            for d, text, sectors in fixed_sector if d in window]
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


def _numbers(e: Event) -> str:
    bits = ([f"expected {e.expected}"] if e.expected else []) + (
        [f"last {e.previous}"] if e.previous else [])
    return f" ({', '.join(bits)})" if bits else ""


def result_line(r: Release) -> str:
    """'Initial Jobless Claims 197K vs 200K expected (below)'."""
    if r.consensus:
        verdict = surprise(r.actual, r.consensus)
        return f"{r.name} {r.actual} vs {r.consensus} expected" + (f" ({verdict})" if verdict else "")
    return f"{r.name} {r.actual}" + (f" (last {r.previous})" if r.previous else "")


def market_text(evts: Iterable[Event], *, president_lines: Sequence[str],
                headlines: Sequence[str], max_events: int = 8,
                results: Sequence[Release] = (), wall_street: Sequence[str] = ()) -> str:
    """One paragraph for Gemma's market line. Short on purpose."""
    wide = [e for e in evts if e.kind in MARKET_WIDE][:max_events]
    parts = []
    came_in = [result_line(r) for r in results if r.actual]
    if came_in:
        parts.append("Last session's data, actual vs expected (a surprise is what moves "
                     "prices): " + "; ".join(came_in[:6]))
    if wide:
        parts.append("Coming up: " + "; ".join(f"{_label(e.day)}: {e.text}{_numbers(e)}"
                                               for e in wide))
    if president_lines:
        parts.append("The President: " + " | ".join(p[:300] for p in president_lines[:3]))
    if headlines:
        parts.append("Top headlines: " + " | ".join(h[:160] for h in headlines[:6]))
    if wall_street:
        parts.append("Wall Street since the last session (ratings, deals, guidance, "
                     "results, the Fed; what the Street did, not a forecast): "
                     + " | ".join(wall_street[:8]))
    parts.append(CONTEXT_NOTE)
    return ". ".join(parts)


def render_md(day: date, evts: Sequence[Event], flag_lines: Sequence[str],
              posts_md: Sequence[str], schedule_md: Sequence[str],
              impacts: Sequence[Impact], unlinked: Sequence[str],
              results: Sequence[Release] = ()) -> str:
    """The operator's copy (logs/week_ahead/YYYY-MM-DD.md)."""
    lines = [f"# Daily brief {day:%A %Y-%m-%d}", "",
             "What is coming, what the news is, which stocks it touches. Gemma reads the same "
             "facts at 05:45. " + CONTEXT_NOTE, "", "## Watch: your positions", ""]
    lines += [f"- ⚠ {f}" for f in flag_lines] or ["- nothing dated against a held position"]
    lines += ["", "## Last session's numbers: actual vs expected", "",
              "_Expected news is mostly priced in; the surprise is what moves prices._", ""]
    lines += [f"- {result_line(r)}" for r in results if r.actual] or ["- nothing published"]
    lines += ["", "## Calendar, next sessions", "", "| Day | What | Kind | Expected | Last |",
              "|---|---|---|---|---|"]
    lines += [f"| {_label(e.day)} | {e.text} | {e.kind} | {e.expected or '-'} | {e.previous or '-'} |"
              for e in evts] or ["| - | nothing found | - | - | - |"]
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


# --- the market dashboard (WORKLIST §61): the operator's view of the whole tape.
# In the operator's copy of the brief only; Gemma's inputs are unchanged.
DASHBOARD: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("US indexes", (("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq Composite"), ("^DJI", "Dow Jones"),
                    ("^RUT", "Russell 2000 (small caps)"),
                    ("RSP", "S&P 500 equal weight (the average stock)"))),
    ("Overnight world", (("ES=F", "S&P 500 futures"), ("NQ=F", "Nasdaq 100 futures"),
                         ("^N225", "Nikkei (Japan)"), ("^HSI", "Hang Seng (Hong Kong)"),
                         ("^GDAXI", "DAX (Germany)"), ("^FTSE", "FTSE (London)"))),
    ("Fear, rates, commodities", (("^VIX", "VIX (fear)"), ("^TNX", "10-year yield"),
                                  ("CL=F", "Crude oil"), ("GC=F", "Gold"),
                                  ("UUP", "US dollar (UUP)"))),
    ("Sectors", (("XLK", "Technology"), ("SMH", "Semiconductors"), ("XLC", "Communication"),
                 ("XLY", "Consumer Discretionary"), ("XLF", "Financials"), ("XLV", "Health Care"),
                 ("XLI", "Industrials"), ("XLE", "Energy"), ("XLB", "Materials"),
                 ("XLRE", "Real Estate"), ("XLU", "Utilities"), ("XLP", "Consumer Staples"))),
)
SORTED_GROUPS = ("Sectors",)


def _chg(cl: Sequence[float], back: int) -> float | None:
    return (cl[-1] / cl[-back - 1] - 1) * 100 if len(cl) > back and cl[-back - 1] else None


def dashboard_md(groups, closes: Mapping[str, Sequence[float]]) -> str:
    """One table per group: 1-day, 5-day, 1-month change and the trend
    (above or below its 20-day average). Sectors sorted by the month."""
    fmt = lambda x: "-" if x is None else f"{x:+.2f}%"
    out = []
    for title, members in groups:
        rows = []
        for sym, name in members:
            cl = [c for c in closes.get(sym, ()) if c]
            if len(cl) < 2:
                continue
            avg20 = sum(cl[-20:]) / len(cl[-20:])
            rows.append((name, _chg(cl, 1), _chg(cl, 5), _chg(cl, 21),
                         "up" if cl[-1] >= avg20 else "down"))
        if title in SORTED_GROUPS:
            rows.sort(key=lambda r: -(r[3] if r[3] is not None else -1e9))
        if rows:
            out += [f"**{title}**", "", "| | 1 day | 5 days | 1 month | trend |",
                    "|---|---|---|---|---|"]
            out += [f"| {n} | {fmt(a)} | {fmt(b)} | {fmt(c)} | {t} |" for n, a, b, c, t in rows]
            out.append("")
    return "\n".join(out)
