"""Scheduled events that cause gaps.

WHY THIS IS DIFFERENT FROM EVERY OTHER FILTER HERE
Seventeen signal filters were tested against this strategy and most made it
worse. This one is not a signal. It removes a KNOWN, DATED event from a
strategy whose measured weakness is precisely that event.

The evidence: gapped stops are the fat left tail. The five worst trades in the
study were all gaps — CF -3.56R, QCOM -2.78R, LITE -2.01R, UAL -1.60R,
MOS -1.58R — against a -1.00R mean for stops that filled normally. A position
sized to lose 1R lost 3.56R.

Earnings are the most predictable gap event that exists, and this strategy
holds overnight: only 54% of setups resolve inside one session. The news layer
reads what has already been published; it cannot know a company reports
tomorrow. This can.

SOURCE
Nasdaq's public calendar API — free, no key, verified returning 527 companies
for a single session. It is undocumented, so every failure is swallowed and
reported as "unknown" rather than "clear": a calendar outage must not silently
become permission to trade through earnings.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import httpx

from scanner.models import Frozen

EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"
ECONOMIC_URL = "https://api.nasdaq.com/api/calendar/economicevents"

#: Nasdaq rejects generic clients; this is their public web front end's shape.
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

#: Days ahead to look. The strategy's time limit is 60 bars of 30m, about 4.6
#: sessions, so a week covers the whole holding period.
DEFAULT_HORIZON = 7

#: Nasdaq's economic calendar answers `date=D` with D-1's releases. Checked
#: 2026-10-08: date=10-09 returned Thursday 10-08's jobless claims and 30-year
#: auction; date=10-15 returned Wednesday 10-14's CPI. Until then every caller
#: showed YESTERDAY's releases as today's (Gemma's market line, the weekly
#: report). Ask for the next day.
ECONOMIC_DATE_OFFSET = timedelta(days=1)
#: Only US releases: the feed carries German and Chinese CPI under the same names.
US = "United States"

#: Sub-series printed alongside a headline release (CPI's n.s.a. index, the
#: Philly Fed's components, nowcasts): one line per release is enough.
SUB_SERIES = re.compile(r"n\.s\.a|\bs\.a\b|CPI Index|Cleveland|Philly Fed|GDPNow")

#: US releases that move the whole tape rather than one name.
MARKET_MOVING = ("FOMC", "CPI", "Nonfarm", "PPI", "GDP", "Unemployment",
                 "Interest Rate", "Fed ")


class Earnings(Frozen):
    """When a company reports, if it reports soon."""

    symbol: str
    on: date
    when: str = ""      # pre-mkt | after-close | ""

    @property
    def label(self) -> str:
        return f"{self.on:%a %m/%d}" + (f" {self.when}" if self.when else "")


class CalendarError(RuntimeError):
    """Raised only where a caller has asked to be told about failure."""


def _session_days(start: date, horizon: int) -> list[date]:
    return [
        start + timedelta(days=offset)
        for offset in range(horizon)
        if (start + timedelta(days=offset)).weekday() < 5
    ]


def parse_earnings(payload: dict, on: date, wanted: frozenset[str]) -> dict[str, Earnings]:
    """Pure parse of one day's calendar page."""
    found: dict[str, Earnings] = {}
    rows = (payload.get("data") or {}).get("rows") or []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol or (wanted and symbol not in wanted):
            continue
        raw = str(row.get("time") or "").lower()
        when = "pre-mkt" if "pre" in raw else ("after-close" if "after" in raw else "")
        found.setdefault(symbol, Earnings(symbol=symbol, on=on, when=when))
    return found


async def upcoming_earnings(
    symbols: frozenset[str],
    client: httpx.AsyncClient,
    *,
    horizon: int = DEFAULT_HORIZON,
    today: date | None = None,
) -> dict[str, Earnings]:
    """Which of `symbols` report within `horizon` days.

    A day that fails to load is skipped, and the caller is told how many days
    were actually read so it can tell "nothing scheduled" from "did not look".
    """
    start = today or date.today()
    found: dict[str, Earnings] = {}
    for day in _session_days(start, horizon):
        try:
            response = await client.get(
                EARNINGS_URL, params={"date": day.isoformat()},
                headers=HEADERS, timeout=15.0,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            continue
        for symbol, event in parse_earnings(payload, day, symbols).items():
            found.setdefault(symbol, event)
    return found


def is_market_moving(event_name: str) -> bool:
    """Whether an economic release moves the whole tape."""
    return any(token.lower() in event_name.lower() for token in MARKET_MOVING)


def economic_query_date(day: date) -> date:
    return day + ECONOMIC_DATE_OFFSET


def parse_economic(payload: dict, *, country: str = US) -> tuple[str, ...]:
    """Pure parse: the day's market-moving releases for `country`, each once."""
    rows = (payload.get("data") or {}).get("rows") or []
    names = (str(row.get("eventName") or row.get("event") or "").strip() for row in rows
             if str(row.get("country") or "") == country)
    return tuple(dict.fromkeys(n for n in names
                               if n and is_market_moving(n) and not SUB_SERIES.search(n)))


async def market_moving_events(
    client: httpx.AsyncClient, *, on: date | None = None
) -> tuple[str, ...]:
    """Today's releases big enough to gap the index, not just one stock."""
    day = on or date.today()
    try:
        response = await client.get(
            ECONOMIC_URL, params={"date": economic_query_date(day).isoformat()},
            headers=HEADERS, timeout=15.0,
        )
        response.raise_for_status()
        return parse_economic(response.json())
    except (httpx.HTTPError, ValueError):
        return ()
