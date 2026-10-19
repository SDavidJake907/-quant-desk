"""TRIALS #73: trade only the morning, 9:30-10:30 ET, flat the rest of the time.

Operator, 2026-10-19: "lets just trade from 8:30am est. to 10:30am". Pre-market
is not in the cache (regular session only), so the window starts at the open.

Per stock, per day (all 501 S&P names in data/intraday_alpaca, 15m bars,
2024-09 -> 2026-09):
  morning : buy at the 9:30 open, sell at the 10:30 close (end of the 10:15 bar)
  hold    : prev close -> close (what the books do now)
  night+morning (secondary): buy at the close, sell at 10:30 the next day
Cost 0.05% per side on each trade; hold pays nothing.

PRIMARY: morning minus hold, mean per day > 0 with t >= 2 and > 0 in both
halves. Secondary needs |t| >= 3.3. Her 7 names shown, not graded.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CACHE = Path("data/intraday_alpaca")
COST = 0.0005
EXIT_BAR = time(10, 15)          # 15m bar starting 10:15 closes at 10:30
HERS = ("COHR", "MPC", "MU", "NVDA", "SNDK", "VLO", "WDC")


def load(sym: str) -> list[tuple]:
    rows = json.loads((CACHE / f"{sym}__15m__session.json").read_text())
    days: dict = defaultdict(list)
    for t, o, _h, _l, c, _v in rows:
        dt = datetime.fromtimestamp(t, ET)
        days[dt.date()].append((dt.time(), o, c))
    return [(d, b) for d, b in sorted(days.items()) if len(b) >= 20 and b[0][0] == time(9, 30)]


def per_day(days) -> dict[str, dict]:
    out: dict = defaultdict(dict)
    for (_, prev), (day, bars) in zip(days, days[1:]):
        exit_px = next((c for t, _o, c in bars if t == EXIT_BAR), None)
        if exit_px is None:
            continue
        pc, o, c = prev[-1][2], bars[0][1], bars[-1][2]
        hold = c / pc - 1
        out["morning"][day] = (exit_px / o - 1 - 2 * COST) - hold
        out["night+morning"][day] = (exit_px / pc - 1 - 2 * COST) - hold
        out["_hold"][day] = hold
        out["_morning_raw"][day] = exit_px / o - 1
    return out


def stats(xs):
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, m / (sd / math.sqrt(n))


def score(per_stock, key):
    by_day: dict = defaultdict(list)
    for s in per_stock:
        for d, r in s[key].items():
            by_day[d].append(r)
    series = [sum(v) / len(v) for _, v in sorted(by_day.items())]
    h = len(series) // 2
    m, t = stats(series)
    return m, t, stats(series[:h])[0], stats(series[h:])[0], len(series)


def main() -> int:
    syms = sorted(p.name.split("__")[0] for p in CACHE.glob("*__15m__session.json"))
    per = {s: per_day(load(s)) for s in syms}
    allp, hers = list(per.values()), [per[s] for s in HERS if s in per]
    for name, group in (("all 501", allp), ("her 7", hers)):
        hm = score(group, "_hold")
        mm = score(group, "_morning_raw")
        print(f"{name}: {hm[4]} days | hold {hm[0]*100:+.3f}%/day ({hm[0]*25200:+.1f}%/yr) | "
              f"9:30-10:30 before costs {mm[0]*100:+.3f}%/day ({mm[0]*25200:+.1f}%/yr)")
        for key in ("morning", "night+morning"):
            m, t, h1, h2, _ = score(group, key)
            print(f"   {key:<14} minus hold {m*100:+.3f}%/day t {t:+.2f}  halves {h1*100:+.3f}% "
                  f"{h2*100:+.3f}%  ({m*25200:+.1f}%/yr)")
    m, t, h1, h2, _ = score(allp, "morning")
    print(f"\nPRIMARY morning vs hold: {'PASS' if m > 0 and t >= 2 and h1 > 0 and h2 > 0 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
