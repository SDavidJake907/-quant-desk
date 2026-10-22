"""Re-score TRIALS #80, #81, #82, #87, #88 and #91 with the P&L-consistent t.

Found 2026-10-22 while sanity-checking the test engine at the operator's challenge
("Are you sure?? it seem like you mistaking somewhere"): those tests computed t over
the MEAN trade of each exit day. When a few days hold very large winners, that
statistic can be negative while the money (the SUM of trades) is positive -- e.g.
#80 1h STC on fast movers: +0.18% excess a trade, but -1.36% a day and t -5.6.
The scalper tests (#83, #84, #92) already used daily SUMS.

Here every test is re-scored with t over each day's SUM of trade results (a book
that takes every signal; the day's total P&L), keeping everything else unchanged.
This is a POST-HOC correction: a version that passes only under it is a candidate
for forward confirmation, not a pass. The pre-registered verdicts stay on record.
READ-ONLY research.
"""

from __future__ import annotations

import asyncio
import math
import statistics
import sys
from collections import defaultdict
from datetime import date
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import alorse_batch as ab  # noqa: E402
import rsi_search as rs  # noqa: E402
import signal_lab as lab  # noqa: E402
import sweep_slow as ss  # noqa: E402


def score(rows, split: date, col: int):
    """(n, mean a trade, t of daily SUMS, t of daily MEANS) per half; col = the result column."""
    out = []
    for half in (True, False):
        r = [x for x in rows if (x[0] < split) == half]
        if len(r) < 3:
            out.append((len(r), float("nan"), float("nan"), float("nan")))
            continue
        by = defaultdict(list)
        for x in r:
            by[x[0]].append(x[col])
        sums = [sum(v) for v in by.values()]
        means = [statistics.mean(v) for v in by.values()]
        t = lambda xs: (statistics.mean(xs) / statistics.stdev(xs) * math.sqrt(len(xs))  # noqa: E731
                        if len(xs) > 2 and statistics.stdev(xs) else float("nan"))
        out.append((len(r), statistics.mean(x[col] for x in r), t(sums), t(means)))
    return out


def show(test: str, name: str, sc, report: list):
    (n1, m1, s1, d1), (n2, m2, s2, d2) = sc
    both_pos = m1 > 0 and m2 > 0
    new_pass = both_pos and s1 >= 3 and s2 >= 3
    old_bar = both_pos and s1 >= 2 and s2 >= 2
    flag = "CANDIDATE (t>=3 on daily totals)" if new_pass else ("t>=2 only" if old_bar else "")
    line = (f"{test:4} {name:26} | H1 {n1:5} {m1:+.3f}% t(sum) {s1:5.1f} [t(mean) {d1:5.1f}] | "
            f"H2 {n2:5} {m2:+.3f}% t(sum) {s2:5.1f} [t(mean) {d2:5.1f}] | {flag}")
    print(line, flush=True)
    report.append((test, name, sc, flag))


def main() -> int:
    report: list = []
    s15 = {p.name.split("__")[0]: lab.load_15m(p) for p in sorted((ROOT / "data/intraday_alpaca").glob("*__15m__session.json"))}
    s1h = {s: lab.to_hourly(b) for s, b in s15.items()}
    s5 = {p.stem: lab.load_5m(p) for p in sorted((ROOT / "data/research_5m").glob("*.json"))}
    stores = {"15m": s15, "1h": s1h, "5m": s5}
    # #80 (fast) and #81 (big): excess column 2
    for test, fixed in (("#80", ()), ("#81", lab.BIGCAP)):
        for tf, series in stores.items():
            res = lab.run_store(tf, series, fixed)
            for k in sorted(res):
                show(test, k, score(res[k], lab.SPLIT, 2), report)
    # #82 RSI search (excess column 2) -- every setting
    for tf, where in product(("15m", "1h"), ("fast", "big")):
        series = stores[tf]
        if where == "fast":
            uni = lab.fast_months({s: lab.daily(b) for s, b in series.items()}, lab.FAST_N[tf])
        else:
            months = {(d.year, d.month) for b in series.values() for d in (x[0] for x in b)}
            uni = {m: set(lab.BIGCAP) for m in months}
        res = rs.search(series, uni)
        for k in sorted(res):
            if len(res[k]) >= 100:
                show("#82", f"{tf} {where} {k}", score(res[k], lab.SPLIT, 2), report)
    # #87 slow sweep (net column 1)
    daily = asyncio.run(ss.daily_series())
    for tf, series in (("1h", s1h), ("daily", daily)):
        months = {(d.year, d.month) for b in series.values() for d in (x[0] for x in b)}
        if tf == "daily":
            fast = lab.fast_months({s: [(d, o, h, lo, c) for d, o, h, lo, c, _ in b] for s, b in series.items()}, 30)
        else:
            fast = lab.fast_months({s: lab.daily(b) for s, b in series.items()}, 30)
        for gname, uni in (("BIG", {m: set(lab.BIGCAP) for m in months}), ("FAST", fast)):
            res, ctl = ss.run(series, uni, tf)
            show("#87", f"{tf} {gname} sweep", score([(d if isinstance(d, date) else d.date(), x) for d, x in res], ss.SPLIT[tf], 1), report)
    # #91 Alorse batch (excess column 2)
    for tf, series in (("1h", s1h), ("daily", daily)):
        months = {(d.year, d.month) for b in series.values() for d in (x[0] for x in b)}
        if tf == "daily":
            fast = lab.fast_months({s: [(d, o, h, lo, c) for d, o, h, lo, c, _ in b] for s, b in series.items()}, 30)
        else:
            fast = lab.fast_months({s: lab.daily(b) for s, b in series.items()}, 30)
        for gname, uni in (("BIG", {m: set(lab.BIGCAP) for m in months}), ("FAST", fast)):
            res = ab.run(series, uni, tf)
            for name in ab.STRATEGIES:
                show("#91", f"{tf} {gname} {name}", score(res.get(name, []), ab.SPLIT[tf], 2), report)
    cands = [r for r in report if r[3].startswith("CANDIDATE")]
    print(f"\nRe-scored {len(report)} versions. Positive in both halves AND t >= 3 on daily totals: {len(cands)}")
    for test, name, sc, _ in cands:
        print(f"   {test} {name}: {sc[0][1]:+.3f}% (t {sc[0][2]:.1f}) / {sc[1][1]:+.3f}% (t {sc[1][2]:.1f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
