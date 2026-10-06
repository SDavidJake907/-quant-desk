"""TradingAgents as a recorded second opinion. Pure: no broker, no subprocess.

WHAT IT IS FOR
TradingAgents (TauricResearch, run from its own venv) puts analyst, debate and
risk agents on one ticker and returns a 5-tier rating. Its ratings are
appended to the audit log as `tradingagents_rating`, so score_gemma.py can
mark them against outcomes the same way it marks the model's own calls.

WHAT IT IS NOT
It never submits, cancels or blocks anything. The scanner's gates and policy
fingerprint decide trades; this records an opinion beside them until the
scorecard shows the opinion carries information.

STAYING OUT OF THE SCAN'S WAY
Both share the one local model. A run is several minutes of back-to-back
calls, and a news-gate call queued behind it during a scan is a delayed
entry, or past `llm_timeout_s` a gate that fails open. So during scan hours a
run starts only with `min_minutes` to spare and is stopped at the deadline.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from scanner.models import AuditEvent, Frozen

#: Scans fire at :03 and :33 from 05:33 to 12:03 local (Alaska), per
#: register_tasks.ps1. The window here runs to the last scan's start.
SCAN_FIRST = (5, 33)
SCAN_LAST = (12, 3)
SCAN_MINUTES = (3, 33)

#: Stop this long before a scan, so the model is free when the gate asks.
DEADLINE_MARGIN = timedelta(minutes=1)

EVENT = "tradingagents_rating"

#: A name whose run failed outright. Counts as handled for the day.
ERROR_EVENT = "tradingagents_error"

#: Exit code of a worker whose own deadline timer fired (see ta_worker.py).
DEADLINE_EXIT = 3


class WorkerResult(Frozen):
    ticker: str
    rating: str
    trader: str = ""
    seconds: float = 0.0
    report: str = ""
    model: str = ""


def pick_tickers(*, held: tuple[str, ...], rows: list[dict],
                 skip: frozenset[str], limit: int,
                 watch: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Held positions first, then the most recent slate, then the scan
    watchlist (advisor/scan_watch.py), deduplicated.

    Held names lead because they are money at risk now; the slate is what the
    scanner last wanted to buy; the watchlist is what the scanner is looking
    at -- the live setups from the last session, ranked by its own score.
    """
    slate: list[str] = []
    for row in reversed(rows):
        if row.get("event") == "candidate_slate" and row.get("slate"):
            slate = [str(item.get("symbol", "")) for item in row["slate"]]
            break
    picked: list[str] = []
    for symbol in (*held, *slate, *watch):
        symbol = symbol.strip().upper()
        if symbol and symbol not in skip and symbol not in picked:
            picked.append(symbol)
    return tuple(picked[:limit])


def rated_on(rows: list[dict], day: date) -> frozenset[str]:
    """Symbols rated -- or failed -- on `day`, so a later pass skips them.

    A failure counts: a delisted name or broken data fails every time, and
    retrying it each pass would spend every gap between scans on one error.
    A pause is not recorded at all, so a paused name is picked up again.
    """
    done = set()
    for row in rows:
        if row.get("event") not in (EVENT, ERROR_EVENT):
            continue
        try:
            when = datetime.fromisoformat(str(row.get("at", "")))
        except ValueError:
            continue
        if when.date() == day:
            done.add(str(row.get("symbol", "")).upper())
    return frozenset(done)


def scan_deadline(now: datetime) -> datetime | None:
    """When a run must be stopped to clear the next scan; None off-hours."""
    first = now.replace(hour=SCAN_FIRST[0], minute=SCAN_FIRST[1], second=0, microsecond=0)
    last = now.replace(hour=SCAN_LAST[0], minute=SCAN_LAST[1], second=0, microsecond=0)
    if not first - timedelta(minutes=30) <= now < last:
        return None
    for minute in SCAN_MINUTES:
        nxt = now.replace(minute=minute, second=0, microsecond=0)
        if nxt > now:
            return nxt - DEADLINE_MARGIN
    nxt = (now + timedelta(hours=1)).replace(minute=SCAN_MINUTES[0], second=0, microsecond=0)
    return nxt - DEADLINE_MARGIN


def _next_scan(now: datetime) -> datetime | None:
    """The next scan start strictly after `now` today, or None after the last."""
    hour, minute = SCAN_FIRST
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    last = now.replace(hour=SCAN_LAST[0], minute=SCAN_LAST[1], second=0, microsecond=0)
    while slot <= last:
        if slot > now:
            return slot
        slot += timedelta(minutes=30)
    return None


def worker_deadline(now: datetime, *, timeout_s: float) -> datetime:
    """When the worker must exit by itself, whatever happens to its parent.

    The driver stops the worker at the scan deadline, but the driver can be
    killed too -- Task Scheduler's time limit ends it and leaves its child
    running, holding the GPU into the next scan. So the worker carries its own
    deadline: the next scan's start less the margin, or the ticker timeout,
    whichever is first. A run begun at 04:50 must still clear 05:33.
    """
    limit = now + timedelta(seconds=timeout_s)
    scan = _next_scan(now)
    return min(limit, scan - DEADLINE_MARGIN) if scan is not None else limit


def stale_checkpoints(files: dict[str, datetime], now: datetime) -> tuple[str, ...]:
    """Checkpoint files last written before today. A checkpoint resumes only
    the same date's run, so an older one can never be used again."""
    return tuple(sorted(name for name, written in files.items()
                        if written.date() < now.date()))


def expired_folders(names, today: date, *, keep_days: int) -> tuple[str, ...]:
    """Dated (YYYY-MM-DD) folders outside the last `keep_days` days, today
    included. Undated folders are kept: they are not ours to judge."""
    cutoff = today - timedelta(days=keep_days - 1)   # today counts as one
    out = []
    for name in names:
        try:
            day = date.fromisoformat(name)
        except ValueError:
            continue
        if day < cutoff:
            out.append(name)
    return tuple(sorted(out))


def may_start(now: datetime, *, min_minutes: float) -> bool:
    deadline = scan_deadline(now)
    return deadline is None or deadline - now >= timedelta(minutes=min_minutes)


def parse_worker_output(stdout: str) -> WorkerResult | None:
    """The worker prints one JSON object as its last line; logging may precede it."""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return None
        known = {k: raw[k] for k in WorkerResult.model_fields if k in raw}
        try:
            return WorkerResult(**known)
        except ValueError:
            return None
    return None


def rating_event(result: WorkerResult, *, mode: str, at: datetime,
                 policy: str, build: str = "") -> AuditEvent:
    """`reason` leads with the rating so the scorecard can split on it."""
    reason = (f"{result.rating}; trader={result.trader or '-'}; mode={mode}; "
              f"secs={result.seconds:.0f}; report={result.report}")
    return AuditEvent(event=EVENT, at=at, symbol=result.ticker, stage="advisor",
                      reason=reason, policy=policy, model=result.model,
                      build=build)


def outcome(*, returncode: int, killed_for: str) -> str:
    """done | paused | failed.

    Paused -- resumable from the checkpoint -- is a deadline from either
    side: the driver's kill at the scan deadline, or the worker's own timer
    (exit DEADLINE_EXIT) when the driver itself was gone or late.
    """
    if killed_for == "scan" or returncode == DEADLINE_EXIT:
        return "paused"
    if killed_for or returncode != 0:
        return "failed"
    return "done"


def error_event(symbol: str, why: str, *, mode: str, at: datetime,
                policy: str, build: str = "") -> AuditEvent:
    return AuditEvent(event=ERROR_EVENT, at=at, symbol=symbol, stage="advisor",
                      reason=f"{why}; mode={mode}", policy=policy, build=build)

