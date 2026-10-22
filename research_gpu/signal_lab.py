"""TRIALS #80: the operator's Pine signals on fast movers -- which buy/sell signal works?

Operator, 2026-10-21: "lets start on this, with the fast moving stocks.. for the
buy/sell signal. find what works best and test it and run it live/papermode".

RULES -- FIXED 2026-10-21 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  signals     scanner/signals/rules.py RULES (11, from the operator's Pine
              downloads), each plain and "+adx" (entry only when ADX(14) > 25)
              = 22 rules
  timeframes  15m (data/intraday_alpaca, 501 S&P names, 2024-09 -> 2026-09),
              1h (the same 15m bars grouped from 9:30), 5m (data/research_5m,
              107 names, 2024-10 -> 2026-10, regular hours only) = 66 versions
  universe    FAST, point in time: each month, the 30 names (20 for the 5m
              store) with the highest 20-day average true range %, measured from
              daily bars built from the same candles up to the prior month's
              last session. A trade may OPEN only in a month its name is fast.
  trades      rules.simulate: next-bar-open entry and exit, 3 x ATR(22) stop,
              flat at Friday's last bar, 0.05% a side
  measure     EXCESS per trade = net % minus the name's own average bar return
              x bars held (its drift in that half), so a rule earns nothing for
              simply being long a rising stock. Trades are averaged per exit
              day; t is over days.
  halves      H1 to 2025-08-31, H2 from 2025-09-01
  PASS        excess > 0 with t >= 2 in BOTH halves. 66 versions are tested at
              once: a single-half t of 2 is expected by luck about 1-2 times, both
              halves together much less.
BIG CAPS (TRIALS #81, operator 2026-10-22: "can we backtest this on a big cap
stock?"), --bigcap: everything above unchanged except the universe -- the 20
largest US companies with intraday data here, FIXED before the run (BIGCAP),
tradeable every month (5m: the 5 of them in that store). Same PASS.

READ-ONLY research. Changes nothing live.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scanner.signals import indicators as ind  # noqa: E402
from scanner.signals import rules  # noqa: E402

ET = ZoneInfo("America/New_York")
SPLIT = date(2025, 9, 1)
FAST_N = {"15m": 30, "1h": 30, "5m": 20}
BIGCAP = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "JPM", "LLY",
          "BRK.B", "V", "WMT", "XOM", "UNH", "MA", "COST", "ORCL", "JNJ", "HD")
OPEN, CLOSE = time(9, 30), time(16, 0)


def load_15m(path: Path):
    rows = json.loads(path.read_text())
    return [(datetime.fromtimestamp(t, ET).replace(tzinfo=None), o, h, lo, c, v) for t, o, h, lo, c, v in rows]


def load_5m(path: Path):
    out = []
    for t, o, h, lo, c, v in json.loads(path.read_text()):
        dt = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(ET).replace(tzinfo=None)
        if OPEN <= dt.time() < CLOSE:
            out.append((dt, o, h, lo, c, v))
    return out


def to_hourly(bars):
    groups: dict = {}
    for dt, o, h, lo, c, v in bars:
        k = (dt.date(), (dt.hour * 60 + dt.minute - 570) // 60)
        g = groups.get(k)
        groups[k] = (dt, o, h, lo, c, v) if g is None else (g[0], g[1], max(g[2], h), min(g[3], lo), c, g[5] + v)
    return [groups[k] for k in sorted(groups)]


def daily(bars):
    days: dict = {}
    for dt, o, h, lo, c, v in bars:
        d = days.get(dt.date())
        days[dt.date()] = (o, h, lo, c) if d is None else (d[0], max(d[1], h), min(d[2], lo), c)
    return sorted((d, *x) for d, x in days.items())


def fast_months(day_bars: dict, n: int) -> dict[tuple[int, int], set[str]]:
    """(year, month) -> the n fastest names by 20-day ATR % at the prior month's end."""
    atrp: dict[str, dict[date, float]] = {}
    for s, rows in day_bars.items():
        vals = {}
        for i in range(20, len(rows)):
            trs = [max(rows[k][2] - rows[k][3], abs(rows[k][2] - rows[k - 1][4]), abs(rows[k][3] - rows[k - 1][4]))
                   for k in range(i - 19, i + 1)]
            vals[rows[i][0]] = sum(trs) / 20 / rows[i][4] * 100
        atrp[s] = vals
    all_days = sorted({d for v in atrp.values() for d in v})
    last_of_month: dict = {}
    for d in all_days:
        last_of_month[(d.year, d.month)] = d
    out = {}
    keys = sorted(last_of_month)
    for prev, cur in zip(keys, keys[1:]):
        d = last_of_month[prev]
        ranked = sorted(((v[d], s) for s, v in atrp.items() if d in v), reverse=True)
        out[cur] = {s for _, s in ranked[:n]}
    return out


def tstat(xs):
    if len(xs) < 3:
        return float("nan")
    sd = statistics.stdev(xs)
    return statistics.mean(xs) / sd * math.sqrt(len(xs)) if sd else float("nan")


def run_store(tf: str, series: dict[str, list], fixed: tuple[str, ...] = ()) -> dict[str, list[tuple[date, float, float, int]]]:
    """variant -> [(exit day, net %, excess %, bars)]. `fixed`: trade these names every month."""
    if fixed:
        months = {(d.year, d.month) for b in series.values() for d in (x[0] for x in b)}
        fast = {m: set(fixed) & set(series) for m in months}
    else:
        fast = fast_months({s: daily(b) for s, b in series.items()}, FAST_N[tf])
    ever = set().union(*fast.values()) if fast else set()
    res: dict[str, list] = defaultdict(list)
    for s in sorted(ever):
        b = series[s]
        if len(b) < 300:
            continue
        t = [x[0] for x in b]
        o, h, lo, c, v = ([x[k] for x in b] for k in range(1, 6))
        may = [s in fast.get((d.year, d.month), ()) for d in t]
        a = ind.atr(h, lo, c, 22)
        ad = ind.adx(h, lo, c)
        adx_ok = [x is not None and x > rules.ADX_MIN for x in ad]
        rets = [None] + [(c[i] / c[i - 1] - 1) * 100 for i in range(1, len(c))]
        drift = {}
        for half in (True, False):
            xs = [r for d, r in zip(t, rets) if r is not None and (d.date() < SPLIT) == half]
            drift[half] = statistics.mean(xs) if xs else 0.0
        for name, (_, rule) in rules.RULES.items():
            state = rule(o, h, lo, c, v)
            for suffix, ok in (("", None), ("+adx", adx_ok)):
                for tr in rules.simulate(s, t, o, h, lo, c, state, entry_ok=ok, may_enter=may, atr_series=a):
                    d = tr.exit_at.date()
                    res[f"{tf} {name}{suffix}"].append(
                        (d, tr.net_pct, tr.net_pct - drift[d < SPLIT] * tr.bars, tr.bars))
    return res


def summarize(rows, half):
    rows = [r for r in rows if (r[0] < SPLIT) == half]
    by_day = defaultdict(list)
    for d, _, ex, _ in rows:
        by_day[d].append(ex)
    days = [statistics.mean(v) for v in by_day.values()]
    return (len(rows), statistics.mean([r[2] for r in rows]) if rows else float("nan"),
            tstat(days), statistics.mean([r[1] for r in rows]) if rows else float("nan"),
            (sum(r[1] > 0 for r in rows) / len(rows) * 100) if rows else float("nan"),
            statistics.mean([r[3] for r in rows]) if rows else float("nan"))


def main() -> int:
    big = "--bigcap" in sys.argv
    stores = {}
    s15 = {p.name.split("__")[0]: load_15m(p) for p in sorted((ROOT / "data/intraday_alpaca").glob("*__15m__session.json"))}
    stores["15m"] = s15
    stores["1h"] = {s: to_hourly(b) for s, b in s15.items()}
    stores["5m"] = {p.stem: load_5m(p) for p in sorted((ROOT / "data/research_5m").glob("*.json"))}
    results = {}
    for tf, series in stores.items():
        t0 = datetime.now()
        results.update(run_store(tf, series, BIGCAP if big else ()))
        print(f"{tf}: {len(series)} names, {(datetime.now() - t0).seconds}s", flush=True)
    print(f"\n{'version':24} | {'H1 n':>5} {'excess':>7} {'t':>5} {'net':>6} {'win':>4} | "
          f"{'H2 n':>5} {'excess':>7} {'t':>5} {'net':>6} {'win':>4} | bars  PASS")
    passed = []
    order = sorted(results, key=lambda k: -summarize(results[k], True)[1])
    for k in order:
        a, b = summarize(results[k], True), summarize(results[k], False)
        ok = a[1] > 0 and a[2] >= 2 and b[1] > 0 and b[2] >= 2
        passed += [k] if ok else []
        print(f"{k:24} | {a[0]:5} {a[1]:+6.2f}% {a[2]:5.1f} {a[3]:+5.2f}% {a[4]:3.0f}% | "
              f"{b[0]:5} {b[1]:+6.2f}% {b[2]:5.1f} {b[3]:+5.2f}% {b[4]:3.0f}% | {b[5]:5.0f}  {'PASS' if ok else ''}")
    print(f"\nPASS (both halves, excess > 0, t >= 2): {', '.join(passed) or 'none'} of {len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
