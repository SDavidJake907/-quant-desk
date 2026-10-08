"""After-hours moves: do they recover, make higher highs, or keep going? (WORKLIST §49)

    python scripts/after_hours_test.py

READ-ONLY. Uses data/intraday_extended (Alpaca SIP 30m bars, 04:00-20:00 ET,
from 2026-06). The after-hours move is the 4pm close to the last print before
8pm. Outcomes are measured from the close (what happened) and from the next
9:30 open (what could still be traded). Split at 2026-08-15 into two halves.
"""
import json, statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")
HELD = {"AMAT","AMD","ARM","INTC","META","MRVL","PLTR","TSM","COHR","FIX","MPC","TER","VLO","MU"}
rows = []            # (sym, date, ah%, overnight_low%, flags...)
for f in Path("data/intraday_extended").glob("*__30m__session.json"):
    sym = f.name.split("__")[0]
    days = defaultdict(lambda: {"pre": [], "post": [], "reg": []})
    for ts, o, h, l, c, v in json.loads(f.read_text()):
        t = datetime.fromtimestamp(ts, ET); hm = t.hour*60+t.minute
        k = "reg" if 570 <= hm < 960 else ("post" if hm >= 960 else "pre")
        days[t.date()][k].append((o, h, l, c))
    ds = [d for d in sorted(days) if len(days[d]["reg"]) >= 12]
    for i in range(len(ds) - 5):
        x = days[ds[i]]
        if not x["post"]: continue
        C0 = x["reg"][-1][3]; H0 = max(b[1] for b in x["reg"])
        ah = (x["post"][-1][3]/C0 - 1)*100
        nxt = days[ds[i+1]]
        night_low = min(b[2] for b in x["post"] + nxt["pre"] + nxt["reg"][:1])
        n1 = nxt["reg"]
        fut = [days[ds[i+j]]["reg"] for j in range(1, 6)]
        rows.append(dict(sym=sym, d=ds[i], ah=ah, low=(night_low/C0-1)*100,
            back1=max(b[1] for b in n1) >= C0,                 # trades back to the close next day
            close1=n1[-1][3] >= C0,                             # closes at/above next day
            close5=any(r[-1][3] >= C0 for r in fut),            # closes back above within 5 days
            hh5=any(max(b[1] for b in r) > H0 for r in fut),    # higher high than the day before, within 5
            ret5=(fut[-1][-1][3]/C0-1)*100, gap=(n1[0][0]/C0-1)*100, o1=(n1[-1][3]/n1[0][0]-1)*100, o5=(fut[-1][-1][3]/n1[0][0]-1)*100))
# market night = median after-hours move across all stocks that night
by_day = defaultdict(list)
for r in rows: by_day[r["d"]].append(r["gap"])
mkt = {d: statistics.fmean(v) for d, v in by_day.items()}
def show(name, rs):
    if not rs: return
    p = lambda k: f"{100*sum(r[k] for r in rs)/len(rs):3.0f}%"
    print(f"{name:42s} n={len(rs):6,d} | back to close next day {p('back1')} | closes above next day {p('close1')}"
          f" | above within 5d {p('close5')} | higher high within 5d {p('hh5')} | 5d return {statistics.fmean(r['ret5'] for r in rs):+.2f}%")

import datetime as dt
SPLIT = dt.date(2026, 8, 15)
def line(name, rs):
    if not rs: print(f"{name:34s} n=0"); return
    m1 = statistics.fmean(r["o1"] for r in rs); m5 = statistics.fmean(r["o5"] for r in rs)
    sd = statistics.pstdev(r["o5"] for r in rs) or 1
    print(f"{name:34s} n={len(rs):6,d} | open->same close {m1:+.2f}% | open->5d close {m5:+.2f}% (t {m5/sd*len(rs)**.5:+.1f})")
for label, lo, hi in (("every night", -99, 99), ("down 1-3%", -3, -1), ("DOWN >3%", -99, -3), ("UP >3%", 3, 99)):
    for half, f in (("Jun-Aug15", lambda r: r["d"] < SPLIT), ("Aug15-Oct", lambda r: r["d"] >= SPLIT)):
        line(f"{label} [{half}]", [r for r in rows if lo <= r["ah"] < hi and f(r)])
