"""The "Now:" line at the top of Gemma's question (operator 2026-10-23).

Her question named the session (regular/pre/after/overnight) but never the
date, the time or the weekday; she had to infer "Friday night, closed for the
weekend" from dates on old records. Asked first, she agreed and asked for two
additions: the time left in the session and the trading days left in the
quarter. Information only: it changes none of her rules.

  Now: Friday 2026-10-23, 7:16 PM ET (after-hours). Next regular session:
  Monday 10-26, 9:30 AM ET (closed Saturday and Sunday). October: 6 trading
  days left; the monthly verdict is Oct 31. Q4: 47 trading days left (ends Dec 31).

PURE: the time and the broker's calendar rows in, text out.
"""

from __future__ import annotations

import calendar as cal
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
NAMES = {"regular": "regular hours", "pre": "premarket", "after": "after-hours",
         "overnight": "overnight", "closed": "market closed"}


def _clock(t: datetime | time) -> str:
    return f"{t.strftime('%I:%M %p').lstrip('0')} ET"


def _span(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _hhmm(text: str) -> time:
    h, m = (int(x) for x in text.split(":"))
    return time(h, m)


def _closed_between(last: date, nxt: date) -> str:
    """The calendar days strictly between two sessions, named; weekdays among them are holidays."""
    days = [last + timedelta(days=i) for i in range(1, (nxt - last).days)]
    if not days:
        return ""
    names = [f"{d:%A}" + (" (holiday)" if d.weekday() < 5 else "") for d in days]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"; closed {joined}"


def now_line(now: datetime, rows: Iterable[Mapping[str, str]], session: str) -> str:
    """`rows` are /v2/calendar rows from today through at least the quarter's end."""
    et = now.astimezone(ET)
    today = et.date()
    days = {date.fromisoformat(r["date"]): (_hhmm(r["open"]), _hhmm(r["close"])) for r in rows}
    parts = [f"Now: {et:%A %Y-%m-%d}, {_clock(et)} ({NAMES.get(session, session)})."]
    hours = days.get(today)
    if hours and session == "regular":
        close_at = datetime.combine(today, hours[1], ET)
        early = " (early close)" if hours[1] < time(16, 0) else ""
        parts.append(f"Regular session closes {_clock(hours[1])}{early}, {_span(close_at - et)} left.")
    elif hours and session == "pre":
        open_at = datetime.combine(today, hours[0], ET)
        parts.append(f"Regular session opens {_clock(hours[0])}, in {_span(open_at - et)}.")
    if session != "regular":
        later = sorted(d for d in days if d > today or (d == today and et.time() < days[d][0]))
        if later and not (later[0] == today and session == "pre"):    # "opens in" said it
            nxt = later[0]
            o, c = days[nxt]
            early = f", closes early {_clock(c)}" if c < time(16, 0) else ""
            anchor = today if today in days else today - timedelta(days=1)
            gap = _closed_between(anchor, nxt) if nxt > today else ""
            parts.append(f"Next regular session: {nxt:%A %m-%d}, {_clock(o)}{early}{gap}.")
    left = [d for d in days if d > today or (d == today and et.time() < days[d][1])]
    month_end = date(today.year, today.month, cal.monthrange(today.year, today.month)[1])
    q_month = ((today.month - 1) // 3 + 1) * 3
    q_end = date(today.year, q_month, cal.monthrange(today.year, q_month)[1])
    in_month = sum(1 for d in left if d <= month_end)
    in_quarter = sum(1 for d in left if d <= q_end)
    parts.append(f"{today:%B}: {in_month} trading day{'s' if in_month != 1 else ''} left; "
                 f"the monthly verdict is {month_end:%b} {month_end.day}.")
    parts.append(f"Q{(today.month - 1) // 3 + 1}: {in_quarter} trading days left "
                 f"(ends {q_end:%b} {q_end.day}).")
    return " ".join(parts)
