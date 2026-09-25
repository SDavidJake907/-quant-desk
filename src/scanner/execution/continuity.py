"""Did this system stop running, and for how long?

THE FAILURE THIS EXISTS FOR
The host was off from 2026-08-24 to 09-24. Twenty-one trading days passed with
no scan, no data collection and no outcome reconciliation. Nothing failed —
the four scheduled tasks were never reached, so there was no error to report,
no non-zero exit, and nothing in any log except an absence. It was found a
month later by a human reading timestamps.

Absence is the one failure a system cannot report about itself while it is
happening. But it CAN report it the moment it comes back, and that is worth
most of the value: the blackout would have been known on the first scan of
2026-09-24 instead of by chance afterwards.

WHY NOT A SIMPLE ELAPSED-TIME ALARM
Scans run every thirty minutes for six and a half hours, then stop for the
night. An overnight gap is seventeen hours and a long weekend is sixty-five,
and both are correct. A threshold in hours either fires every single morning
or is set so high it misses a week. So this counts MISSED SESSIONS — weekdays
that went by with nothing recorded — which is zero on every normal night and
every ordinary weekend.

Weekday arithmetic rather than the exchange calendar, deliberately. A holiday
makes this over-count by one, and an alert that occasionally says "one session
missed" over Thanksgiving is a far smaller problem than one that needs a
broker call to decide whether to warn. It is a smoke detector, not a clock.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from scanner.models import Frozen

#: Missed weekdays before this is worth saying out loud. One is enough: a
#: single missed session is a scheduler that did not fire, and the point is
#: to hear about it on day one rather than after twenty-one.
ALERT_SESSIONS = 1


class Gap(Frozen):
    """A stretch during which the system recorded nothing."""

    last_seen: datetime
    now: datetime
    missed_sessions: int

    @property
    def days(self) -> float:
        return (self.now - self.last_seen).total_seconds() / 86_400

    @property
    def is_alerting(self) -> bool:
        return self.missed_sessions >= ALERT_SESSIONS

    def describe(self) -> str:
        return (f"nothing recorded for {self.days:.1f} days "
                f"({self.missed_sessions} trading day(s) missed) — "
                f"last activity {self.last_seen:%Y-%m-%d %H:%M}")


def missed_sessions(last_seen: datetime, now: datetime) -> int:
    """Weekdays that passed with nothing recorded.

    A day counts only when it is wholly inside the gap: the day of the last
    record is not missed, because something WAS recorded on it, and today is
    not missed either, because this run is happening on it.
    """
    if now <= last_seen:
        return 0
    first: date = last_seen.date() + timedelta(days=1)
    last: date = now.date() - timedelta(days=1)
    missed = 0
    day = first
    while day <= last:
        if day.weekday() < 5:
            missed += 1
        day += timedelta(days=1)
    return missed


def last_activity(path: str | Path) -> datetime | None:
    """When this system last wrote anything at all.

    Reads the audit log, which is the system's own record of having acted.
    A missing or unreadable log returns None — a first run is not a gap, and
    inventing one would cry wolf on every fresh install.
    """
    log = Path(path)
    if not log.exists():
        return None
    latest: datetime | None = None
    try:
        with log.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    stamp = json.loads(line).get("at")
                except json.JSONDecodeError:
                    continue
                if not stamp:
                    continue
                try:
                    when = datetime.fromisoformat(str(stamp))
                except ValueError:
                    continue
                if latest is None or when > latest:
                    latest = when
    except OSError:
        return None
    return latest


def check(path: str | Path, *, now: datetime | None = None) -> Gap | None:
    """The gap since the last recorded activity, or None if there is none."""
    last_seen = last_activity(path)
    if last_seen is None:
        return None
    moment = now or datetime.now().astimezone()
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=moment.tzinfo)
    return Gap(
        last_seen=last_seen,
        now=moment,
        missed_sessions=missed_sessions(last_seen, moment),
    )
