"""When in the session do setups work? Entry bar from the open to the close.

    python scripts/time_of_day_test.py                            # unseen 2018 store
    python scripts/time_of_day_test.py --store data/intraday_alpaca \\
        --since 2024-09-01 --until 2026-12-31                     # the 2y store

READ-ONLY research. Changes nothing live. Same baseline trades as
gate1_test.py (live gates, Mon-Thu, Friday flatten, 10bps), cached in tmp/.

Bands are the session's own 30-minute bars (time of the trigger bar's open,
ET) and the weekday -- no threshold is chosen, so nothing here is tuned. Each
band is shown for the first and second half of the trades by time: a band is
worth a look only if it points the same way in both. With 13 bars and 4 days
compared, one or two will look special by chance.

OPENING-BAR RULE. FIXED 2026-10-05 AFTER THE 2Y RUN, COMMITTED BEFORE THE 2018
STORE WAS READ BY THIS SCRIPT.
On the 2y store the 09:30 ET trigger bar (entry ~10:00 ET, the 06:03 AK scan)
was -0.260R on 255 setups, negative in both halves; the rest about +0.02R.
    rule       skip setups whose trigger bar opens at 09:30 ET
    stated     the opening bar resolves WORSE than the rest of the session
    judged     on the unseen store: gap, permutation p, account with the rule,
               and the sign in each Gate 1 period
"Same day vs overnight" is printed but is NOT a usable split: a trade ends the
same day mostly because it was stopped, so it restates the outcome.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from datetime import date

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, "scripts")
from gate1_test import STORE, load_trades, report  # noqa: E402

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
AK_OFFSET = 4          # Alaska = ET - 4


def cell(rs: list[float]) -> str:
    if not rs:
        return f"{'-':>22}"
    win = sum(r > 0 for r in rs) / len(rs) * 100
    return f"{statistics.fmean(rs):+.3f}R n={len(rs):<4} {win:>3.0f}%"


def table(trades, key, order, title: str) -> None:
    half = len(trades) // 2
    parts = (("all", trades), ("1st half", trades[:half]), ("2nd half", trades[half:]))
    print(f"\n== {title}   (1st half to {trades[half - 1]['at']:%Y-%m-%d})")
    print(f"   {'':<16}" + "".join(f"{name:<25}" for name, _ in parts) + "same sign")
    for band in order:
        groups = [[t["r"] for t in part if key(t) == band] for _, part in parts]
        if not groups[0]:
            continue
        means = [statistics.fmean(g) if g else 0.0 for g in groups[1:]]
        same = "yes" if groups[1] and groups[2] and (means[0] > 0) == (means[1] > 0) else ""
        print(f"   {band:<16}" + "".join(f"{cell(g):<25}" for g in groups) + same)


def bar_label(t) -> str:
    et = t["at"]
    ak = (et.hour - AK_OFFSET) % 24
    return f"{et:%H:%M} ET {ak:02d}:{et:%M}AK"


def main(store: str, since: date, until: date, workers: int) -> int:
    trades = load_trades(store, since, until, workers)
    print(f"{len(trades)} gated setups {trades[0]['at']:%Y-%m-%d}..{trades[-1]['at']:%Y-%m-%d}")
    order = sorted({bar_label(t) for t in trades})
    table(trades, bar_label, order, "BY TRIGGER BAR (entry at its close)")
    session = lambda t: ("open 09:30-10:30" if t["at"].hour < 10 or (t["at"].hour == 10 and t["at"].minute == 0)
                         else "close 14:30-16:00" if (t["at"].hour, t["at"].minute) >= (14, 30)
                         else "midday")
    table(trades, session, ("open 09:30-10:30", "midday", "close 14:30-16:00"), "BY PART OF SESSION")
    table(trades, lambda t: DAYS[t["at"].weekday()], DAYS, "BY WEEKDAY")
    opening = [t for t in trades if (t["at"].hour, t["at"].minute) == (9, 30)]
    rest = [t for t in trades if (t["at"].hour, t["at"].minute) != (9, 30)]
    report("OPENING-BAR RULE: skip the 09:30 ET trigger (kept = the rest)", rest, opening)
    held = defaultdict(list)
    for t in trades:
        days = (t["exit_at"].date() - t["at"].date()).days
        held["same day" if days == 0 else "overnight+"].append(t["r"])
    print("\n== HOW LONG THEY WERE HELD")
    for name, rs in held.items():
        print(f"   {name:<16}{cell(rs)}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--store", default=STORE)
    p.add_argument("--since", type=date.fromisoformat, default=date(2018, 1, 1))
    p.add_argument("--until", type=date.fromisoformat, default=date(2024, 8, 31))
    p.add_argument("--workers", type=int, default=6)
    a = p.parse_args()
    raise SystemExit(main(a.store, a.since, a.until, a.workers))
