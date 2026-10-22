"""TRIALS #99: do US stocks drift down in the ASIA or LONDON session -- sell before it, buy back after?

Operator, 2026-10-22: "if we sold during Asia hours, or london.. where the market going
sideways or negtive. that would be a time to sell before that. and buy back when its ready
to trend back up."

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  data      the 24/5 series of #97 (SIP 04:00-20:00 + BOATS 20:00-04:00, 30m), 2024-09-16 on
  sessions  (ET) AFTER 16:00-20:00, ASIA 20:00-03:00, LONDON 03:00-09:30, US 09:30-16:00;
            a session's return = first bar's open -> last bar's close inside it, Mon-Thu
            nights for the overnight ones (no Friday-night session)
  groups    OURS (the 10 names the books hold on 10-22: AAPL AMZN AMGN MET NVDA MRVL DDOG
            MPC MU VLO), BIG, FAST_UP (#97's daily rule), ALL (the collected sample)
  report 1  mean session return per group, t over nights (equal-weight book)
  rule U    sell at the start of the session with the most negative half-1 mean, buy back at
            its end, every night: the gain = MINUS that session's return, less 0.20% (two
            extended-hours fills at 0.10%)
  rule C    "when it is going sideways or negative": sell at 20:00 ONLY if AFTER was < 0,
            buy back at 03:00; gain = minus ASIA on those nights, less 0.20%
  PASS      the rule's mean gain a night > 0 with t >= 3 in half 2 (rule U's session chosen
            on half 1); halves split 2025-09-16
READ-ONLY research.
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from datetime import date, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import around_clock as ac  # noqa: E402
import signal_lab as lab  # noqa: E402

OURS = ("AAPL", "AMZN", "AMGN", "MET", "NVDA", "MRVL", "DDOG", "MPC", "MU", "VLO")
COST = 0.20
SPLIT = ac.SPLIT


def sessions(bars) -> dict[date, dict[str, float]]:
    """trading day d -> % return of AFTER (d 16-20), ASIA (d 20 -> d+1 03), LONDON (d+1 03-09:30), US (d 09:30-16)."""
    by: dict[date, list] = defaultdict(list)
    for b in bars:
        by[b[0].date()].append(b)

    def span(rows, t0, t1):
        rows = [r for r in rows if t0 <= r[0].time() < t1]
        return (rows[0][1], rows[-1][4]) if rows else None

    out: dict[date, dict[str, float]] = {}
    days = sorted(by)
    for d, nd in zip(days, days[1:]):
        r = {}
        us = span(by[d], time(9, 30), time(16, 0))
        if us:
            r["US"] = (us[1] / us[0] - 1) * 100
        if d.weekday() <= 3 and nd == d + timedelta(days=1):
            aft = span(by[d], time(16, 0), time(20, 0))
            asia_a = [x for x in by[d] if x[0].time() >= time(20, 0)] + [x for x in by[nd] if x[0].time() < time(3, 0)]
            lon = span(by[nd], time(3, 0), time(9, 30))
            if aft:
                r["AFTER"] = (aft[1] / aft[0] - 1) * 100
            if asia_a:
                r["ASIA"] = (asia_a[-1][4] / asia_a[0][1] - 1) * 100
            if lon:
                r["LONDON"] = (lon[1] / lon[0] - 1) * 100
        out[d] = r
    return out


def main() -> int:
    names = sorted({p.name.split("__")[0] for p in (ROOT / "data/intraday_ext_2024").glob("*__30m__session.json")})
    bars24 = {s: ac.load_24(s) for s in names}
    bars24 = {s: b for s, b in bars24.items() if len(b) > 500}
    dailies = {s: ac.daily_regular(b) for s, b in bars24.items()}
    fu = ac.fast_up_by_day(dailies)
    sess = {s: sessions(b) for s, b in bars24.items()}
    groups = {"OURS": lambda d: set(OURS), "BIG": lambda d: set(lab.BIGCAP), "FAST_UP": lambda d: fu.get(d, set()),
              "ALL": lambda d: set(bars24)}
    passes = 0
    for gname, members in groups.items():
        book: dict[str, dict[date, float]] = defaultdict(dict)
        cond: dict[date, list] = defaultdict(list)
        for k in ("AFTER", "ASIA", "LONDON", "US"):
            per: dict[date, list] = defaultdict(list)
            for s, days in sess.items():
                for d, r in days.items():
                    if s in members(d) and k in r:
                        per[d].append(r[k])
                        if k == "ASIA" and r.get("AFTER") is not None and r["AFTER"] < 0:
                            cond[d].append(-r["ASIA"] - COST)
            book[k] = {d: statistics.mean(v) for d, v in per.items() if v}
        print(f"\n== {gname} ==  mean % per session (equal-weight book)")
        for k, v in book.items():
            a = [x for d, x in v.items() if d < SPLIT]
            b = [x for d, x in v.items() if d >= SPLIT]
            print(f"   {k:7} half 1: {len(a):3} days {statistics.mean(a):+.3f}% t {ac.tstat(a):5.1f} | "
                  f"half 2: {len(b):3} days {statistics.mean(b):+.3f}% t {ac.tstat(b):5.1f}")
        worst = min(("AFTER", "ASIA", "LONDON"), key=lambda k: statistics.mean([x for d, x in book[k].items() if d < SPLIT]))
        u2 = [-x - COST for d, x in book[worst].items() if d >= SPLIT]
        c2 = [statistics.mean(v) for d, v in cond.items() if d >= SPLIT and v]
        ok_u = statistics.mean(u2) > 0 and ac.tstat(u2) >= 3
        ok_c = bool(c2) and statistics.mean(c2) > 0 and ac.tstat(c2) >= 3
        passes += ok_u + ok_c
        print(f"   rule U: sit out {worst} (weakest in half 1) -> half 2 gain {statistics.mean(u2):+.3f}% a night after 0.20% costs, "
              f"t {ac.tstat(u2):.1f}: {'PASS' if ok_u else 'FAIL'}")
        print(f"   rule C: sit out ASIA after a red evening -> half 2 gain {statistics.mean(c2) if c2 else float('nan'):+.3f}% "
              f"({len(c2)} nights), t {ac.tstat(c2):.1f}: {'PASS' if ok_c else 'FAIL'}")
    print(f"\nPASS: {passes} of 8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
