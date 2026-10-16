"""VAL-CALENDAR 2026 U.S. Economic Calendar (Guggenheim Investments).

Fixed economic release dates and market holidays from VAL-CALENDAR.pdf.
These are used as additional fixed events in the daily brief, supplementing
the Nasdaq API data queried at runtime. The Nasdaq API takes precedence when
available; this calendar provides a fallback and cross-check.

SOURCE: VAL-CALENDAR.pdf (Guggenheim Investments,
VAL-CALENDAR-1225 x1226 #67020)

WHY THIS MATTERS
Economic releases (CPI, PPI, PCE, FOMC, Employment) are among the most
predictable gap events. Knowing their dates in advance lets the system:
  - Refuse entries ahead of high-impact releases (via block_earnings_within_days
    analogy for macro events)
  - Warn Gemma about upcoming macro risk in her morning brief
  - Cross-check Nasdaq API data for accuracy

All dates are 2026 U.S. market dates.
"""

from __future__ import annotations

from datetime import date
from typing import Final

#: FOMC Policy Decision Announcements (market-moving, whole tape)
FOMC_2026: Final[tuple[date, ...]] = (
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
)

#: CPI releases (Consumer Price Index, market-moving)
CPI_2026: Final[tuple[date, ...]] = (
    date(2026, 1, 13),
    date(2026, 2, 11),
    date(2026, 3, 11),
    date(2026, 4, 10),
    date(2026, 5, 12),
    date(2026, 6, 10),
    date(2026, 7, 14),
    date(2026, 8, 12),
    date(2026, 9, 11),
    date(2026, 10, 14),
    date(2026, 11, 10),
    date(2026, 12, 10),
)

#: PPI releases (Producer Price Index, market-moving)
PPI_2026: Final[tuple[date, ...]] = (
    date(2026, 1, 14),
    date(2026, 2, 12),
    date(2026, 3, 12),
    date(2026, 4, 14),
    date(2026, 5, 13),
    date(2026, 6, 11),
    date(2026, 7, 15),
    date(2026, 8, 13),
    date(2026, 9, 11),
    date(2026, 10, 15),
    date(2026, 11, 13),
    date(2026, 12, 15),
)

#: PCE releases (Personal Consumption Expenditures, market-moving)
PCE_2026: Final[tuple[date, ...]] = (
    date(2026, 1, 29),
    date(2026, 2, 26),
    date(2026, 3, 27),
    date(2026, 4, 30),
    date(2026, 5, 28),
    date(2026, 6, 25),
    date(2026, 7, 30),
    date(2026, 8, 26),
    date(2026, 9, 30),
    date(2026, 10, 29),
    date(2026, 11, 25),
    date(2026, 12, 23),
)

#: Employment Report (BLS, market-moving)
EMPLOYMENT_2026: Final[tuple[date, ...]] = (
    date(2026, 1, 9),
    date(2026, 2, 6),
    date(2026, 3, 6),
    date(2026, 4, 3),
    date(2026, 5, 8),
    date(2026, 6, 5),
    date(2026, 7, 2),
    date(2026, 8, 7),
    date(2026, 9, 4),
    date(2026, 10, 2),
    date(2026, 11, 6),
    date(2026, 12, 4),
)

#: ISM PMI (manufacturing, monthly around the 1st-5th)
ISM_PMI_2026: Final[tuple[date, ...]] = (
    date(2026, 1, 5),
    date(2026, 2, 2),
    date(2026, 3, 3),
    date(2026, 4, 1),
    date(2026, 5, 1),
    date(2026, 6, 1),
    date(2026, 7, 1),
    date(2026, 8, 3),
    date(2026, 9, 1),
    date(2026, 10, 1),
    date(2026, 11, 2),
    date(2026, 12, 1),
)

#: Market holidays (exchange closed)
MARKET_HOLIDAYS_2026: Final[tuple[date, ...]] = (
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # Martin Luther King Jr. Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day Observed
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
)


def fomc_events() -> tuple[tuple[date, str, str], ...]:
    """FOMC events as (date, kind, text) tuples for FIXED_EVENTS."""
    return tuple(
        (d, "fed", f"FOMC {'meeting starts' if d.month in (1, 6, 12) else 'rate decision'}")
        for d in FOMC_2026
    )


def economic_events() -> tuple[tuple[date, str, str], ...]:
    """All economic release events as (date, kind, text) tuples."""
    events: list[tuple[date, str, str]] = []
    for d in CPI_2026:
        events.append((d, "econ", "CPI"))
    for d in PPI_2026:
        events.append((d, "econ", "PPI"))
    for d in PCE_2026:
        events.append((d, "econ", "PCE"))
    for d in EMPLOYMENT_2026:
        events.append((d, "econ", "Employment Report"))
    for d in ISM_PMI_2026:
        events.append((d, "econ", "ISM PMI"))
    return tuple(sorted(events, key=lambda e: e[0]))


def holiday_events() -> tuple[tuple[date, str, str], ...]:
    """Market holiday closures as (date, kind, text) tuples."""
    names = {
        date(2026, 1, 1): "New Year's Day",
        date(2026, 1, 19): "Martin Luther King Jr. Day",
        date(2026, 2, 16): "Presidents' Day",
        date(2026, 4, 3): "Good Friday",
        date(2026, 5, 25): "Memorial Day",
        date(2026, 6, 19): "Juneteenth",
        date(2026, 7, 3): "Independence Day Observed",
        date(2026, 9, 7): "Labor Day",
        date(2026, 11, 26): "Thanksgiving",
        date(2026, 12, 25): "Christmas",
    }
    return tuple(
        (d, "market", f"market CLOSED ({names[d]})")
        for d in sorted(MARKET_HOLIDAYS_2026)
    )


def all_val_events() -> tuple[tuple[date, str, str], ...]:
    """All VAL-CALENDAR events combined."""
    return fomc_events() + economic_events() + holiday_events()
