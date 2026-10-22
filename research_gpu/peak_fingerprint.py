"""TRIALS #106: the operator's PEAK FINGERPRINT as a sell signal.

Operator, 2026-10-22: the account's highest point was Wed 10-21 14:30 ET ($101,683). "create a promt
for Gemma using those indicators % when to sell for each postion that is open., maybe a little lower
so it hits the mark everytime". The readings at that moment (data/research_peak_10-21_1430.json),
thresholds set about 10% LOWER, fixed here before any history was looked at:
  1h  Schaff STC >= 85        1h  Connors RSI >= 63
  1d  Schaff STC >= 80        1d  %B (20, 2) >= 0.80
  (5m trend tools all up and price >= 0.25% over VWAP: not testable on this history -- the 5m
   store covers 107 names only; the 1h/1d part is tested here)
FINGERPRINT = all four 1h/1d conditions true on a completed 1-hour candle (daily values from the
prior completed day plus the current price).

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  data     data/intraday_alpaca 15m -> 1h (501 S&P names, 2024-09 -> 2026-09); daily from the
           same candles
  question when the fingerprint fires, does the stock do WORSE than usual afterwards? -- the
           return from the next hour's open to the close 1 hour, 1 day (7 hours) and 5 days
           (35 hours) later, MINUS the same name's average return over that length (its drift)
  PASS     as a sell signal: mean excess < 0 for the 1-day horizon in BOTH halves (split
           2025-09-01) with t <= -3 over fire days (daily totals); events at most once per name
           per day
  also     how often it fires (an exit that fires every few days on every stock is a churn rule)
READ-ONLY research.
"""

from __future__ import annotations

import math
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import signal_lab as lab  # noqa: E402

from scanner.signals import indicators as ind  # noqa: E402

SPLIT = date(2025, 9, 1)
HORIZONS = {"1 hour": 1, "1 day": 7, "5 days": 35}


def pct_b(c, n=20):
    out = [None] * len(c)
    for i in range(n - 1, len(c)):
        w = c[i - n + 1:i + 1]
        m = sum(w) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / n)
        out[i] = (c[i] - (m - 2 * sd)) / (4 * sd) if sd else None
    return out


def main() -> int:
    res = {k: [] for k in HORIZONS}
    fires = names_n = hours_n = 0
    for p in sorted((ROOT / "data/intraday_alpaca").glob("*__15m__session.json")):
        b = lab.to_hourly(lab.load_15m(p))
        if len(b) < 400:
            continue
        names_n += 1
        t = [x[0] for x in b]
        o, c = [x[1] for x in b], [x[4] for x in b]
        stc_h, crsi_h = ind.stc(c), ind.connors_rsi(c)
        # daily series and per-day indicators (completed days)
        days = {}
        for x in b:
            days[x[0].date()] = x[4]
        dlist = sorted(days)
        dclose = [days[d] for d in dlist]
        dstc, dpb = ind.stc(dclose), pct_b(dclose)
        dprev = {d: i - 1 for i, d in enumerate(dlist)}
        drift = {k: [] for k in HORIZONS}
        events_today = set()
        for i in range(60, len(c) - 36):
            hours_n += 1
            for k, hz in HORIZONS.items():
                drift[k].append((t[i].date(), c[i + hz] / o[i + 1] - 1))
            di = dprev.get(t[i].date())
            if di is None or di < 0 or dstc[di] is None or dpb[di] is None or stc_h[i] is None or crsi_h[i] is None:
                continue
            if not (stc_h[i] >= 85 and crsi_h[i] >= 63 and dstc[di] >= 80 and dpb[di] >= 0.80):
                continue
            if t[i].date() in events_today:
                continue
            events_today.add(t[i].date())
            fires += 1
            for k, hz in HORIZONS.items():
                res[k].append((t[i].date(), c[i + hz] / o[i + 1] - 1, p.name.split("__")[0]))
        means = {k: {h: statistics.mean([r for d, r in v if (d < SPLIT) == h]) for h in (True, False)
                     if [r for d, r in v if (d < SPLIT) == h]} for k, v in drift.items()}
        for k in HORIZONS:
            res[k] = [(d, r - means[k].get(d < SPLIT, 0.0), s) if s == p.name.split("__")[0] else (d, r, s)
                      for d, r, s in res[k]]
    print(f"{names_n} names, {hours_n:,} name-hours; the fingerprint fired {fires:,} times "
          f"(about once every {hours_n / max(fires, 1) / 7:.0f} trading days per stock)")
    ok = True
    for k, rows in res.items():
        out = []
        for half in (True, False):
            r = [x * 100 for d, x, _ in rows if (d < SPLIT) == half]
            by = defaultdict(float)
            for d, x, _ in rows:
                if (d < SPLIT) == half:
                    by[d] += x * 100
            days_v = list(by.values())
            t = statistics.mean(days_v) / statistics.stdev(days_v) * math.sqrt(len(days_v)) if len(days_v) > 2 else float("nan")
            out.append((statistics.mean(r), t, len(r), sum(x < 0 for x in r) / len(r) * 100))
        print(f"  after the fingerprint, next {k:7}: 2024-25 {out[0][0]:+.3f}% vs normal (t {out[0][1]:.1f}, {out[0][2]} events, fell {out[0][3]:.0f}% of the time) | "
              f"2025-26 {out[1][0]:+.3f}% (t {out[1][1]:.1f}, {out[1][2]}, fell {out[1][3]:.0f}%)")
        if k == "1 day":
            ok = out[0][0] < 0 and out[1][0] < 0 and out[0][1] <= -3 and out[1][1] <= -3
    print(f"\nAs a sell signal: {'PASS' if ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
