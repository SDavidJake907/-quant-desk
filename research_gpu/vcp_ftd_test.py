"""Minervini's VCP and O'Neil's follow-through / distribution days, tested with
their own code (research_gpu/external/tradermonty, MIT, unchanged).

    <GPU python> research_gpu/vcp_ftd_test.py

READ-ONLY research. CPU (multiprocessing); the calculators are pure Python.

FIXED 2026-10-06, COMMITTED BEFORE THIS FILE FIRST RAN. TRIALS.md #67-#69, ONE
family of three, so the bar is t >= 2.4 (Bonferroni for 3).

1 VCP (stocks; panels LIVE S&P 500+400 2015-26 and CRASH S&P 500 1997-2016)
  As of the close of day d-1: calculate_trend_template(...)["passed"] with
  rs_rank = the stock's 252-day-return percentile across the panel that day,
  quote price/yearHigh/yearLow from the last 252 days; and
  calculate_vcp_pattern(...) valid_vcp with a pivot. Volume spike on day d:
  volume >= 1.5 x the 50 prior days' average.
    VCP event  template passed, valid VCP, close(d) > pivot, volume spike
    CONTROL    template passed, close(d) above the prior 20-day high, volume
               spike, up day, and NOT a VCP event -- an ordinary Stage-2
               volume breakout, so the test isolates the CONTRACTION idea
  One event per stock per 10 days; index members only. Outcome: return from
  the close of d+1 over 20 days (60 shown), minus the members' average.
  PASS: VCP minus control per month > 0 with Newey-West t >= 2.4 on LIVE, and
  positive on CRASH.
2 FTD (S&P 500 ^GSPC, 1997-2026): analyze_single_index on the 120 days to d
  reports an FTD dated d. Outcome: S&P return from the close of d+1 over 60
  trading days, minus the average 60-day return over all days.
  PASS: mean > 0 with t >= 2.4, and positive in 1997-2010 and 2011-2026.
3 Distribution days: at each month-end, _count_distribution_days on the last
  25 sessions. Outcome: S&P return over the next 21 days from the close of
  d+1. PASS: months with 5+ distribution days return LESS than months with
  4 or fewer, Welch t <= -2.4, same sign in both halves.
"""

from __future__ import annotations

import math
import pickle
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "external" / "tradermonty"))
import distribution_day_calculator as ddc  # noqa: E402
import rally_tracker as rt                  # noqa: E402
import trend_template_calculator as ttc     # noqa: E402
import vcp_pattern_calculator as vpc        # noqa: E402

H, H_LONG, T_BAR, GAP_DAYS, WORKERS = 20, 60, 2.4, 10, 8
PANELS = ROOT.parent / "data" / "factors"
SPLIT = "2011-01-01"


def nw_t(xs, lag=2):
    n = len(xs)
    if n < lag + 3:
        return 0.0
    m = sum(xs) / n
    d = [x - m for x in xs]
    var = sum(v * v for v in d) / n
    for k in range(1, lag + 1):
        var += 2 * (1 - k / (lag + 1)) * sum(d[i] * d[i - k] for i in range(k, n)) / n
    return m / math.sqrt(var / n) if var > 0 else 0.0


def welch_t(a, b):
    if len(a) < 3 or len(b) < 3:
        return 0.0
    va, vb = statistics.variance(a) / len(a), statistics.variance(b) / len(b)
    return (statistics.fmean(a) - statistics.fmean(b)) / math.sqrt(va + vb)


# ---------------------------------------------------------------- VCP (stocks)
def _history(dates, o, h, lo, c, v, end):
    """Most-recent-first OHLCV dicts for days <= end (the calculators' format)."""
    start = max(0, end - 259)
    return [{"date": dates[i], "open": o[i], "high": h[i], "low": lo[i], "close": c[i], "volume": v[i]}
            for i in range(end, start - 1, -1)]


def _stock(args):
    j, dates, o, h, lo, c, v, rs, member_days, fwd_ex20, fwd_ex60 = args
    events, last = [], -10 ** 9
    t = len(c)
    for d in range(261, t - 1 - H_LONG):
        if not member_days[d] or np.isnan(c[d]) or np.isnan(c[d - 1]) or np.isnan(fwd_ex20[d]):
            continue
        avg50 = np.nanmean(v[d - 50:d])
        if not (avg50 > 0 and v[d] >= 1.5 * avg50 and c[d] > c[d - 1]):
            continue
        window = c[d - 200:d]
        if np.isnan(window).any() or c[d - 1] <= window.mean():          # cheap necessary filter
            continue
        hist = _history(dates, o, h, lo, c, v, d - 1)
        quote = {"price": c[d - 1], "yearHigh": float(np.nanmax(h[d - 252:d])),
                 "yearLow": float(np.nanmin(lo[d - 252:d]))}
        rank = None if np.isnan(rs[d - 1]) else int(rs[d - 1])
        if not ttc.calculate_trend_template(hist, quote, rs_rank=rank).get("passed"):
            continue
        vcp = vpc.calculate_vcp_pattern(hist)
        is_vcp = bool(vcp.get("valid_vcp")) and vcp.get("pivot_price") and c[d] > vcp["pivot_price"]
        breakout20 = c[d] > np.nanmax(h[d - 20:d])
        if not (is_vcp or breakout20) or d - last < GAP_DAYS:
            continue
        last = d
        events.append(("VCP" if is_vcp else "CTRL", dates[d][:7], float(fwd_ex20[d]), float(fwd_ex60[d])))
    return events


def vcp_panel(name):
    p = pickle.loads((PANELS / f"panel_{name}.pkl").read_bytes())
    t, n = len(p["dates"]), len(p["symbols"])
    arr = {k: np.frombuffer(p[k], dtype=np.float64).reshape(t, n) for k in ("open", "high", "low", "close", "volume")}
    c = arr["close"]
    ret252 = np.full_like(c, np.nan)
    ret252[252:] = c[252:] / c[:-252] - 1
    rs = np.full_like(c, np.nan)
    for d in range(252, t):
        row, ok = ret252[d], ~np.isnan(ret252[d])
        if ok.sum() > 10:
            rs[d, ok] = np.argsort(np.argsort(row[ok])) / (ok.sum() - 1) * 99
    ends = sorted(int(k) for k in p["members"])
    member = np.zeros((t, n), dtype=bool)
    k = 0
    for d in range(t):
        while k + 1 < len(ends) and ends[k + 1] <= d:
            k += 1
        if ends and ends[0] <= d:
            member[d, list(p["members"][ends[k]])] = True
    fwd = {}
    for hh in (H, H_LONG):
        f = np.full_like(c, np.nan)
        f[: t - 1 - hh] = c[1 + hh:] / c[1: t - hh] - 1
        masked = np.where(member, f, np.nan)
        fwd[hh] = f - np.nanmean(masked, axis=1, keepdims=True)
    jobs = [(j, p["dates"], arr["open"][:, j], arr["high"][:, j], arr["low"][:, j], c[:, j],
             arr["volume"][:, j], rs[:, j], member[:, j], fwd[H][:, j], fwd[H_LONG][:, j]) for j in range(n)]
    with ProcessPoolExecutor(WORKERS) as pool:
        events = [e for chunk in pool.map(_stock, jobs, chunksize=8) for e in chunk]
    by = {g: defaultdict(list) for g in ("VCP", "CTRL")}
    for g, month, e20, _ in events:
        by[g][month].append(e20)
    months = sorted(m for m in by["VCP"] if by["CTRL"].get(m))
    gap = [statistics.fmean(by["VCP"][m]) - statistics.fmean(by["CTRL"][m]) for m in months]
    def mean_of(g, i):
        xs = [e[i] for e in events if e[0] == g]
        return statistics.fmean(xs) * 100 if xs else float("nan"), len(xs)
    (v20, nv), (c20, nc) = mean_of("VCP", 2), mean_of("CTRL", 2)
    v60, c60 = mean_of("VCP", 3)[0], mean_of("CTRL", 3)[0]
    out = {"n_vcp": nv, "n_ctrl": nc, "vcp20": v20, "ctrl20": c20, "vcp60": v60, "ctrl60": c60,
           "gap": statistics.fmean(gap) * 100 if gap else float("nan"), "t": nw_t(gap), "months": len(months)}
    print(f"\nVCP {name}: {nv} VCP breakouts, {nc} control Stage-2 breakouts")
    print(f"   20d excess  VCP {v20:+.2f}%  control {c20:+.2f}%   |   60d  VCP {v60:+.2f}%  control {c60:+.2f}%")
    print(f"   VCP minus control per month: {out['gap']:+.2f}pp  NW t {out['t']:.2f}  ({len(months)} months)")
    return out


# ---------------------------------------------------------------- index tests
def index_history():
    x = pickle.loads((PANELS / "index_GSPC.pkl").read_bytes())
    return [{"date": d, "open": o, "high": h, "low": lo, "close": c, "volume": v}
            for d, o, h, lo, c, v in zip(x["dates"], x["open"], x["high"], x["low"], x["close"], x["volume"])]


def ftd_test(hist):
    c = [r["close"] for r in hist]
    t = len(c)
    fwd = [c[d + 1 + H_LONG] / c[d + 1] - 1 if d + 1 + H_LONG < t else None for d in range(t)]
    events = []
    for d in range(300, t - 1 - H_LONG):
        res = rt.analyze_single_index(hist[d - 119:d + 1], "S&P 500")
        ftd = res.get("ftd") or {}
        if ftd.get("ftd_detected") and ftd.get("ftd_date") == hist[d]["date"]:
            events.append((hist[d]["date"], fwd[d]))
    base = statistics.fmean(f for f in fwd[300:t - 1 - H_LONG] if f is not None)
    ex = [f - base for _, f in events]
    halves = [[f - base for day, f in events if (day < SPLIT) == early] for early in (True, False)]
    tstat = statistics.fmean(ex) / (statistics.stdev(ex) / math.sqrt(len(ex))) if len(ex) > 2 else 0.0
    print(f"\nFTD S&P 500 {hist[300]['date']}..{hist[t - 1 - H_LONG]['date']}: {len(events)} follow-through days")
    print(f"   60d return after FTD minus the all-days average {base * 100:+.2f}%: {statistics.fmean(ex) * 100:+.2f}pp  "
          f"t {tstat:.2f}   1997-2010 {statistics.fmean(halves[0]) * 100:+.2f}pp (n {len(halves[0])})  "
          f"2011-26 {statistics.fmean(halves[1]) * 100:+.2f}pp (n {len(halves[1])})")
    return {"n": len(events), "ex": statistics.fmean(ex), "t": tstat,
            "halves": [statistics.fmean(h_) if h_ else float("nan") for h_ in halves]}


def dd_test(hist):
    c = [r["close"] for r in hist]
    t = len(c)
    rows = []
    for d in range(300, t - 1 - 21):
        if hist[d + 1]["date"][:7] == hist[d]["date"][:7]:
            continue                                                     # month-ends only
        count = ddc._count_distribution_days(list(reversed(hist[d - 25:d + 1])), "S&P 500")["distribution_days"]
        rows.append((hist[d]["date"], count, c[d + 1 + 21] / c[d + 1] - 1))
    hi = [r for _, n, r in rows if n >= 5]
    lo = [r for _, n, r in rows if n <= 4]
    tstat = welch_t(hi, lo)
    halves = []
    for early in (True, False):
        h1 = [r for day, n, r in rows if n >= 5 and (day < SPLIT) == early]
        l1 = [r for day, n, r in rows if n <= 4 and (day < SPLIT) == early]
        halves.append(statistics.fmean(h1) - statistics.fmean(l1) if h1 and l1 else float("nan"))
    print(f"\nDISTRIBUTION DAYS, month-ends: {len(hi)} months with 5+, {len(lo)} with 4 or fewer")
    print(f"   next 21d S&P: 5+ {statistics.fmean(hi) * 100:+.2f}%  vs  <=4 {statistics.fmean(lo) * 100:+.2f}%   "
          f"Welch t {tstat:.2f}   1997-2010 {halves[0] * 100:+.2f}pp  2011-26 {halves[1] * 100:+.2f}pp")
    return {"t": tstat, "diff": statistics.fmean(hi) - statistics.fmean(lo), "halves": halves}


def main() -> int:
    hist = index_history()
    ftd, dd = ftd_test(hist), dd_test(hist)
    live, crash = vcp_panel("LIVE"), vcp_panel("CRASH")
    print("\nPASS (t >= 2.4, family of three)")
    print(f"   VCP  LIVE gap {live['gap']:+.2f}pp t {live['t']:.2f}, CRASH gap {crash['gap']:+.2f}pp  ->  "
          f"{'PASS' if live['gap'] > 0 and live['t'] >= T_BAR and crash['gap'] > 0 else 'fail'}")
    print(f"   FTD  {ftd['ex'] * 100:+.2f}pp t {ftd['t']:.2f}, halves {ftd['halves'][0] * 100:+.2f} / {ftd['halves'][1] * 100:+.2f}  ->  "
          f"{'PASS' if ftd['ex'] > 0 and ftd['t'] >= T_BAR and all(x > 0 for x in ftd['halves']) else 'fail'}")
    print(f"   DD   {dd['diff'] * 100:+.2f}pp t {dd['t']:.2f}, halves {dd['halves'][0] * 100:+.2f} / {dd['halves'][1] * 100:+.2f}  ->  "
          f"{'PASS' if dd['t'] <= -T_BAR and all(x < 0 for x in dd['halves']) else 'fail'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
