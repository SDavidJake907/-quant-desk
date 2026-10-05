"""GO_LIVE Gate 1: does the live strategy, and does any window-16 candidate, hold
on data none of it was found on?

    python scripts/gate1_test.py                                  # the unseen store
    python scripts/gate1_test.py --store data/intraday_alpaca \\
        --since 2024-09-01 --until 2026-12-31                     # sanity: the 2y store

READ-ONLY research. Changes nothing live.

DATA
data/intraday_alpaca_2018, 30m SIP session bars, cut at 2024-08-31 so it does
not overlap the 2y store every idea was found on. Universe = today's 501
S&P 500 names, so delisted and dropped companies are absent (survivorship:
biases every long result UP; a fail here is a strong fail, a pass is weaker).

THE BASELINE, AS LIVE RUNS IT (same machinery as position_size_test.py)
Every Mon-Thu setup that passes the live price, $20M/bar, stop >= 1.5% and
reward:risk >= 1.5 gates. Entry at the trigger close, the bracket, the FRIDAY
FLATTEN, max_hold_bars. R charged 10bps round trip on position value.
Account: 5 slots, 20% cap, 1% risk, compounding, cash-only, $100k.
Not modelled: the news gate (no headlines before 2019 and no labels), the
relative-strength floor (as in every earlier baseline), the +0.30% entry limit
and late-signal guard (fills assumed at the trigger close).

GATE 1 CRITERIA (GO_LIVE.md, unchanged)
  expectancy > 0, account ends positive, max DD < 25%, account positive in at
  least two of 2018-19 / 2020 / 2021 / 2022-23. 2024-01..08 is shown, not scored.

CANDIDATE RULES -- FIXED 2026-10-05, COMMITTED BEFORE THIS SCRIPT FIRST RAN ON
THE 2018 STORE. Each is a filter on the baseline's setups. Reported: the kept
vs excluded mean R with a two-sided permutation p, the kept set per period, and
the kept set's account under the same 5-slot simulation.
  1 reward:risk    keep planned reward:risk >= 2.0
  2 sector         sector median 12-1 RS vs ^GSPC from daily closes strictly
                   before the trigger date (GICS from data/constituents.csv);
                   keep leading (>= +5pp); also leading vs lagging (<= -5pp)
  3 fast movers    keep daily ATR >= 4.8% of price: 14-day mean true range on
                   daily bars built from the store's session bars, strictly
                   before the trigger date
  7 insiders       keep setups with any open-market purchase (Form 4 code P)
                   FILED in the 90 calendar days before the trigger date
  8 short volume   FINRA short_ratio, mean of the 5 trading days before the
                   trigger date minus the mean of the 60 before those; keep
                   rising (>= +0.03); also rising vs falling (<= -0.03).
                   Direction stated: rising short flow into a sweep resolves
                   BETTER (sellers trapped under the swept low). Setups before
                   2018-11 have no reading and are left out of this one only.
Candidates 4 (news), 5 (MPC) and 6 (momentum engine) use different engines and
are run by their own scripts.
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import csv
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STORE = "data/intraday_alpaca_2018"
COST = 0.0010
PERIODS = (("2018-19", date(2018, 1, 1), date(2019, 12, 31), True),
           ("2020", date(2020, 1, 1), date(2020, 12, 31), True),
           ("2021", date(2021, 1, 1), date(2021, 12, 31), True),
           ("2022-23", date(2022, 1, 1), date(2023, 12, 31), True),
           ("2024-01..08", date(2024, 1, 1), date(2024, 8, 31), False))
LEAD, LAG = 5.0, -5.0
FAST_ATR = 4.8
INSIDER_DAYS = 90
SHORT_RECENT, SHORT_BASE, SHORT_MOVE = 5, 60, 0.03
DRAWS = 5_000


def _daily(bars):
    """(dates, highs, lows, closes) per session from 30m bars."""
    out: dict[date, list[float]] = {}
    for t, h, lo, c in zip(bars.times, bars.highs, bars.lows, bars.closes):
        d = t.date()
        if d in out:
            row = out[d]
            out[d] = [max(row[0], h), min(row[1], lo), c]
        else:
            out[d] = [h, lo, c]
    dates = sorted(out)
    return dates, [out[d][0] for d in dates], [out[d][1] for d in dates], [out[d][2] for d in dates]


def _atr_pct(daily, when: date) -> float | None:
    dates, highs, lows, closes = daily
    cut = bisect.bisect_left(dates, when)
    if cut < 15:
        return None
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(cut - 14, cut)]
    return statistics.fmean(trs) / closes[cut - 1] * 100


def _work(args):
    """One symbol: every gated Mon-Thu setup in [since, until] with its exit."""
    symbol, store, since, until = args
    sys.path.insert(0, "scripts")
    from scanner.execution.policy import TradingPolicy
    from scanner.execution.session import crosses_session
    from scanner.features import history
    from scanner.features.intraday import SESSION_ALIGNED
    from scanner.screens.smc import scan
    from gemma_graded import gates_for
    from position_size_test import exit_of

    v = TradingPolicy()
    history.STORE_DIR = Path(store)
    bars = history.accumulated(symbol, v.interval_minutes, SESSION_ALIGNED)
    if bars is None or len(bars) < 60:
        return []
    daily = _daily(bars)
    rows = []
    for setup in scan(bars, k=v.swing_k, require_value_sweep=v.require_value_sweep,
                      min_risk_pct=v.min_risk_pct, min_reward_risk=v.min_reward_risk):
        i = setup.trigger_index
        when = bars.times[i]
        risk = setup.entry_price - setup.stop_price
        if not since <= when.date() <= until or risk <= 0 or crosses_session(bars, i):
            continue
        if when.weekday() >= v.wind_down_from_weekday or i + v.max_hold_bars > len(bars) - 1:
            continue
        if not all(ok for _, ok, _ in gates_for(bars.upto(i), setup, None)):
            continue
        price, idx = exit_of(bars, setup)
        rows.append({"symbol": symbol, "at": when, "exit_at": bars.times[idx],
                     "entry": setup.entry_price, "stop": setup.stop_price, "exit": price,
                     "r": (price - setup.entry_price) / risk - COST * setup.entry_price / risk,
                     "rr": (setup.target_price - setup.entry_price) / risk,
                     "atrd": _atr_pct(daily, when.date())})
    return rows


class SectorRS:
    """Point-in-time sector median 12-1 RS vs ^GSPC, bisect-indexed and cached."""

    def __init__(self, daily, sector_of, bench):
        self._members = defaultdict(list)
        for symbol, series in daily.items():
            if sector_of.get(symbol):
                self._members[sector_of[symbol]].append((list(series.dates), list(series.closes)))
        self._bench = (list(bench.dates), list(bench.closes))
        self._cache: dict = {}

    @staticmethod
    def _mom(dates, closes, when):
        from relative_strength_test import momentum_12_1
        return momentum_12_1(closes[:bisect.bisect_left(dates, when)])

    def at(self, sector, when):
        key = (sector, when)
        if key not in self._cache:
            index = self._mom(*self._bench, when)
            values = [m - index for m in (self._mom(d, c, when) for d, c in self._members[sector])
                      if m is not None and index is not None]
            self._cache[key] = statistics.median(values) if len(values) >= 5 else None
        return self._cache[key]


async def sector_feature(rows, symbols):
    import httpx
    from scanner.features.bars import fetch_bars, fetch_many
    from sector_leadership_test import sectors
    sector_of = sectors()
    async with httpx.AsyncClient(timeout=60) as client:
        bench = await fetch_bars("^GSPC", client, range_="10y")
        daily, errors = await fetch_many(tuple(symbols), client, range_="10y")
    if errors:
        print(f"  sector: {len(errors)} symbols failed to fetch daily bars")
    rs = SectorRS(daily, sector_of, bench)
    for row in rows:
        sector = sector_of.get(row["symbol"])
        row["sector_rs"] = rs.at(sector, row["at"].date()) if sector else None


def insider_feature(rows, path=Path("data/insiders/form4.csv")):
    buys: dict[str, list[date]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for rec in csv.DictReader(handle):
            if rec["code"] == "P":
                buys[rec["symbol"]].append(date.fromisoformat(rec["filing_date"]))
    for dates in buys.values():
        dates.sort()
    for row in rows:
        d, dates = row["at"].date(), buys.get(row["symbol"], [])
        lo = bisect.bisect_left(dates, d - timedelta(days=INSIDER_DAYS))
        row["insider"] = bisect.bisect_left(dates, d) > lo


def short_feature(rows, folder=Path("data/short_volume")):
    series: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for f in sorted(folder.glob("*.csv")):
        with f.open(encoding="utf-8") as handle:
            for rec in csv.DictReader(handle):
                series[rec["symbol"]].append((date.fromisoformat(rec["date"]), float(rec["short_ratio"])))
    for s in series.values():
        s.sort()
    for row in rows:
        s = series.get(row["symbol"], [])
        cut = bisect.bisect_left(s, (row["at"].date(),))
        if cut < SHORT_RECENT + SHORT_BASE:
            row["short_move"] = None
            continue
        recent = [x for _, x in s[cut - SHORT_RECENT:cut]]
        base = [x for _, x in s[cut - SHORT_RECENT - SHORT_BASE:cut - SHORT_RECENT]]
        row["short_move"] = statistics.fmean(recent) - statistics.fmean(base)


def stats(rs):
    if not rs:
        return "n=0"
    se = statistics.stdev(rs) / len(rs) ** 0.5 if len(rs) > 1 else 0.0
    win = sum(r > 0 for r in rs) / len(rs) * 100
    return f"{statistics.fmean(rs):+.3f}R se {se:.3f} n={len(rs)} win {win:.0f}%"


def account(trades):
    from position_size_test import simulate
    return simulate(sorted(trades, key=lambda t: t["at"]), 20.0, 1.0)


def in_period(t, lo, hi):
    return lo <= t["at"].date() <= hi


def report(name, kept, rest=None, compare=None):
    from relative_strength_test import permutation_p
    print(f"\n== {name}")
    print(f"   kept      {stats([t['r'] for t in kept])}")
    a, b = compare if compare else (kept, rest)
    if b is not None:
        ra, rb = [t["r"] for t in a], [t["r"] for t in b]
        if ra and rb:
            gap = statistics.fmean(ra) - statistics.fmean(rb)
            label = "vs excluded" if compare is None else "a vs b"
            print(f"   {label:<10}{stats(rb)}   gap {gap:+.3f}R  p={permutation_p(ra, rb, DRAWS):.3f}")
    acct = account(kept) if kept else None
    if acct:
        print(f"   account   {acct['return']:+.1f}%  max DD {acct['dd']:.1f}%  taken {acct['taken']}")
    scored_pos = 0
    for label, lo, hi, scored in PERIODS:
        part = [t for t in kept if in_period(t, lo, hi)]
        if not part:
            continue
        acc = account(part)
        mark = "" if scored else "  (not scored)"
        scored_pos += scored and acc["return"] > 0
        print(f"   {label:<12}{stats([t['r'] for t in part]):<44} account {acc['return']:+6.1f}%"
              f"  DD {acc['dd']:4.1f}%{mark}")
    return acct, scored_pos


def verdict(trades):
    rs = [t["r"] for t in trades]
    acct, periods_pos = report("BASELINE (live rules)", trades)
    checks = (("expectancy > 0", statistics.fmean(rs) > 0),
              ("account ends positive", acct["return"] > 0),
              ("max DD < 25%", acct["dd"] < 25),
              ("positive in >= 2 of 4 periods", periods_pos >= 2))
    print("\n   GATE 1 (baseline):")
    for label, ok in checks:
        print(f"     [{'x' if ok else ' '}] {label}")


def main(store: str, since: date, until: date, workers: int) -> int:
    symbols = sorted({f.name.split("__")[0] for f in Path(store).glob("*.json")})
    print(f"scanning {len(symbols)} symbols in {store}, {since}..{until}, {workers} workers")
    with ProcessPoolExecutor(workers) as pool:
        trades = [row for rows in pool.map(_work, [(s, store, since, until) for s in symbols])
                  for row in rows]
    trades.sort(key=lambda t: t["at"])
    print(f"{len(trades)} gated setups {trades[0]['at']:%Y-%m-%d}..{trades[-1]['at']:%Y-%m-%d}")
    asyncio.run(sector_feature(trades, symbols))
    insider_feature(trades)
    short_feature(trades)

    verdict(trades)
    split = lambda key: ([t for t in trades if key(t)], [t for t in trades if not key(t)])
    report("1 reward:risk >= 2.0", *split(lambda t: t["rr"] >= 2.0))
    lead = [t for t in trades if t["sector_rs"] is not None and t["sector_rs"] >= LEAD]
    lag = [t for t in trades if t["sector_rs"] is not None and t["sector_rs"] <= LAG]
    report("2 sector leading (compare: leading vs lagging)", lead, compare=(lead, lag))
    report("3 daily ATR >= 4.8%", *split(lambda t: (t["atrd"] or 0) >= FAST_ATR))
    report("7 insider purchase filed in prior 90d", *split(lambda t: t["insider"]))
    measured = [t for t in trades if t["short_move"] is not None]
    rising = [t for t in measured if t["short_move"] >= SHORT_MOVE]
    falling = [t for t in measured if t["short_move"] <= -SHORT_MOVE]
    report("8 short ratio rising (compare: rising vs falling)", rising, compare=(rising, falling))
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
