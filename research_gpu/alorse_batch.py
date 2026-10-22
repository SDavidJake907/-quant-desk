"""TRIALS #91: five new strategies from github.com/Alorse/pinescript-strategies, run by PineTS.

Operator, 2026-10-22: "yes" -- install PineTS, check the translations, then test the
genuinely new ideas as ONE batch. The buy/sell conditions come from the original Pine
(tools/pinets/alorse_signals.pine, executed by PineTS on our candles); trading rules
are this desk's standard.

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  strategies TTM Squeeze, BB Winner PRO, Williams Vix Fix, QQE signals, Range Filter
             (long side only; state = rules._machine(buy, sell))
  candles    1h: data/intraday_alpaca 15m grouped from 9:30 (2024-09 -> 2026-09),
             halves split 2025-09-01; daily: Yahoo 10y of today's S&P 500, split
             2021-01-01
  groups     BIG (signal_lab.BIGCAP) and FAST (monthly fastest 30, point in time)
  trades     rules.simulate: next-open fills, 3 x ATR(22) stop, flat at Friday's
             last candle, 0.05% a side; entries only in months the name is in its group
  measure    EXCESS = net % minus the name's drift x candles held (#80); t over exit days
  PASS       excess > 0 with t >= 3 in BOTH halves (5 strategies x 2 timeframes x
             2 groups = 20 versions; ~840 tested before this batch)
READ-ONLY research.
"""

from __future__ import annotations

import asyncio
import json
import math
import statistics
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import signal_lab as lab  # noqa: E402
import sweep_slow as ss  # noqa: E402

from scanner.signals import indicators as ind  # noqa: E402
from scanner.signals import rules  # noqa: E402

STRATEGIES = ("ttm", "bbwin", "vixfix", "qqe", "rangefilter")
SPLIT = {"1h": date(2025, 9, 1), "daily": date(2021, 1, 1)}
PINE = ROOT / "tools/pinets/alorse_signals.pine"


def signals(bars) -> dict[str, list[int]]:
    rows = []
    for t, o, h, lo, c, v in bars:
        ms = int((t if isinstance(t, datetime) else datetime(t.year, t.month, t.day, tzinfo=timezone.utc)).timestamp() * 1000)
        rows.append({"open": o, "high": h, "low": lo, "close": c, "volume": v, "openTime": ms})
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(rows, f)
    out = subprocess.run(["node", str(ROOT / "tools/pinets/run_pine.mjs"), f.name, str(PINE)],
                         capture_output=True, text=True, check=True, cwd=ROOT / "tools/pinets")
    Path(f.name).unlink(missing_ok=True)
    return json.loads(out.stdout)


def as_dt(t):
    return t if isinstance(t, datetime) else datetime(t.year, t.month, t.day, 16, 0)


def run(series: dict, uni: dict, tf: str) -> dict[str, list]:
    res: dict[str, list] = defaultdict(list)
    for s in sorted(set().union(*uni.values()) & set(series)):
        b = series[s]
        if len(b) < 300:
            continue
        t = [as_dt(x[0]) for x in b]
        o, h, lo, c, v = ([x[k] for x in b] for k in range(1, 6))
        try:
            sig = signals(b)
        except subprocess.CalledProcessError as exc:
            print(f"  {s}: PineTS failed ({exc.stderr[:120]})")
            continue
        may = [s in uni.get((d.year, d.month), ()) for d in t]
        a = ind.atr(h, lo, c, 22)
        rets = [None] + [(c[i] / c[i - 1] - 1) * 100 for i in range(1, len(c))]
        drift = {}
        for half in (True, False):
            xs = [r for d, r in zip(t, rets) if r is not None and (d.date() < SPLIT[tf]) == half]
            drift[half] = statistics.mean(xs) if xs else 0.0
        for name in STRATEGIES:
            state = rules._machine([x == 1 for x in sig[f"{name}_B"]], [x == 1 for x in sig[f"{name}_S"]])
            for tr in rules.simulate(s, t, o, h, lo, c, state, may_enter=may, atr_series=a):
                d = tr.exit_at.date()
                res[name].append((d, tr.net_pct, tr.net_pct - drift[d < SPLIT[tf]] * tr.bars))
    return res


def summary(rows):
    by = defaultdict(list)
    for d, _, ex in rows:
        by[d].append(ex)
    days = [statistics.mean(v) for v in by.values()]
    t = statistics.mean(days) / statistics.stdev(days) * math.sqrt(len(days)) if len(days) > 2 and statistics.stdev(days) else float("nan")
    ex = statistics.mean(r[2] for r in rows) if rows else float("nan")
    net = statistics.mean(r[1] for r in rows) if rows else float("nan")
    win = sum(r[1] > 0 for r in rows) / len(rows) * 100 if rows else float("nan")
    return len(rows), ex, t, net, win


def main() -> int:
    s15 = {p.name.split("__")[0]: lab.load_15m(p) for p in sorted((ROOT / "data/intraday_alpaca").glob("*__15m__session.json"))}
    stores = {"1h": {s: lab.to_hourly(b) for s, b in s15.items()}, "daily": asyncio.run(ss.daily_series())}
    passes = 0
    for tf, series in stores.items():
        months = {(d.year, d.month) for b in series.values() for d in (x[0] for x in b)}
        if tf == "daily":
            fast = lab.fast_months({s: [(d, o, h, lo, c) for d, o, h, lo, c, _ in b] for s, b in series.items()}, 30)
        else:
            fast = lab.fast_months({s: lab.daily(b) for s, b in series.items()}, 30)
        for gname, uni in (("BIG", {m: set(lab.BIGCAP) for m in months}), ("FAST", fast)):
            res = run(series, uni, tf)
            print(f"\n== {tf} {gname} ==")
            for name in STRATEGIES:
                rows = res.get(name, [])
                A = summary([r for r in rows if r[0] < SPLIT[tf]])
                B = summary([r for r in rows if r[0] >= SPLIT[tf]])
                ok = A[1] > 0 and A[2] >= 3 and B[1] > 0 and B[2] >= 3
                passes += ok
                print(f"   {name:11} | half 1: {A[0]:5} trades excess {A[1]:+.2f}% t {A[2]:5.1f} net {A[3]:+.2f}% win {A[4]:3.0f}% | "
                      f"half 2: {B[0]:5} excess {B[1]:+.2f}% t {B[2]:5.1f} net {B[3]:+.2f}% win {B[4]:3.0f}% | {'PASS' if ok else 'FAIL'}", flush=True)
    print(f"\nPASS: {passes} of 20")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
