"""Can a published crash guard keep momentum's edge and cut its 2008 crash?

    python scripts/momentum_guard_test.py

READ-ONLY research. Changes nothing live.

WHY
WORKLIST §31: top-10 momentum beat random by +2.96pp/hold in the 400 and
+0.83pp in the 600 (2016-26), but on the S&P 500 in 2000-15 it was ZERO
against random (t 0.22), with a -49% book and a 62% drawdown. The operator
chose the universe on 2026-10-05: S&P 500 + MidCap 400.

DATASETS
  CRASH  S&P 500, 2000-2015, point-in-time (drift <= 2%). The MidCap 400
         log starts 2012 (drift 12% before), so the 400 cannot join here.
  LIVE   S&P 500 + MidCap 400, 2016-2026, point-in-time union. 400 drift
         6.5% in 2016, 3.5% in 2018, 1% by 2021 -- early years approximate.
Both survivor-biased (delisted names unpriced): returns read high, drawdowns
low. "Momentum minus random" under identical rules is the comparison.

ARMS -- FIXED 2026-10-05, COMMITTED BEFORE THIS SCRIPT RAN. All from
published work, none tuned here. Same 12-1 signal, 63-day hold, a new book
every 21 days, 25bps per hold; the random control gets the SAME guard.
  base    top 10, always invested (the design that crashed)
  A trend top 10, but a book is held in CASH when the S&P 500's close the day
          before entry is below its 210-day average (Faber's 10-month rule)
  B vol   top 10, exposure = min(1, 15% / the S&P 500's annualised daily
          volatility over the 126 days before entry), rest in cash.
          SIMPLIFIED from Barroso & Santa-Clara, who scale by the momentum
          portfolio's own volatility; market volatility is used here.
  C broad top 30, always invested

PASS, per arm, ALL THREE (decided now):
  1  CRASH max drawdown of the three-tranche account < 25%
  2  CRASH momentum minus random >= 0 per hold
  3  LIVE  momentum minus random > 0 per hold, Newey-West t (lag 2) >= 1.5
Drawdown is sampled every 21 days, so it UNDERSTATES; 25% is a ceiling on an
understated number, not a promise.
"""

from __future__ import annotations

import asyncio
import random
import statistics
import sys
from collections import defaultdict
from datetime import date

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.backtest import engine
from scanner.features.bars import Bars, fetch_bars, fetch_many
from scanner.sources import index_history
from scanner.sources.index_history import SP400, SP500

sys.path.insert(0, "scripts")
from backtest import SCREENS  # noqa: E402
from momentum_hac_test import newey_west_t  # noqa: E402

HORIZON, STEP, COST_BPS = 63, 21, 25
SEEDS = (1, 2, 3, 4, 5)
SMA_DAYS, VOL_DAYS, VOL_TARGET = 210, 126, 15.0
DATASETS = (("CRASH", (SP500,), date(1999, 6, 1), date(2016, 3, 31), "30y"),
            ("LIVE", (SP500, SP400), date(2015, 6, 1), date(2026, 10, 2), "12y"))
MAX_DD, MIN_T = 25.0, 1.5


def window(bars: Bars, lo: date, hi: date) -> Bars | None:
    keep = [i for i, d in enumerate(bars.dates) if lo <= d <= hi]
    if not keep:
        return None
    a, b = keep[0], keep[-1] + 1
    return bars.model_copy(update={f: getattr(bars, f)[a:b] for f in
                                   ("dates", "opens", "closes", "volumes", "highs", "lows")})


def union_membership(histories):
    cache: dict[date, frozenset[str]] = {}

    def members_on(on: date) -> frozenset[str]:
        if on not in cache:
            cache[on] = frozenset().union(*(h.members_on(on, collapse_share_classes=True)
                                            for h in histories))
        return cache[on]
    return members_on


def books(result) -> dict[date, float]:
    by = defaultdict(list)
    for t in result.trades:
        by[t.entry_date].append(t.trade_return - COST_BPS / 100)
    return {d: statistics.fmean(v) for d, v in by.items()}


def exposure(bench: Bars, entry: date, arm: str) -> float:
    """Fraction invested for a book entered on `entry`, from S&P data before it."""
    cut = sum(1 for d in bench.dates if d < entry)
    closes = bench.closes[:cut]
    if arm == "A trend":
        return 1.0 if len(closes) > SMA_DAYS and closes[-1] > statistics.fmean(closes[-SMA_DAYS:]) else 0.0
    if arm == "B vol":
        if len(closes) <= VOL_DAYS:
            return 1.0
        rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - VOL_DAYS, len(closes))]
        vol = statistics.stdev(rets) * 252 ** 0.5 * 100
        return min(1.0, VOL_TARGET / vol) if vol else 1.0
    return 1.0


def account(series: list[float]) -> tuple[float, float, float]:
    """Three overlapping tranches. (total %, max DD %, worst book %)."""
    equity, peak, worst_dd = [1 / 3] * 3, 1.0, 0.0
    for i, r in enumerate(series):
        equity[i % 3] *= 1 + r / 100
        total = sum(equity)
        peak = max(peak, total)
        worst_dd = max(worst_dd, (peak - total) / peak)
    return (sum(equity) - 1) * 100, worst_dd * 100, min(series)


async def run_dataset(name, specs, lo, hi, range_, client) -> dict[str, dict]:
    histories = [await index_history.load(client, spec=s) for s in specs]
    symbols = tuple(sorted(set().union(*(h.symbols_ever(lo, hi, collapse_share_classes=True)
                                         for h in histories))))
    bench = window(await fetch_bars("^GSPC", client, range_=range_), lo, hi)
    raw, errors = await fetch_many(symbols, client, range_=range_)
    bars = {s: w for s, b in raw.items() if (w := window(b, lo, hi)) is not None and len(w) > 300}
    for h in histories:
        bars, _ = engine.drop_recycled(bars, h.exit_dates(lo, hi))
    members = union_membership(histories)
    run = lambda screen, n, all_=True: engine.run(bars, bench, SCREENS[screen], horizon_days=HORIZON,
                                                  step_days=STEP, top_n=n, require_all=all_,
                                                  membership=members)
    mom10, mom30 = books(run("momentum", 10)), books(run("momentum", 30))
    rand: dict[date, list[float]] = defaultdict(list)
    for seed in SEEDS:
        random.seed(seed)
        for d, r in books(run("random", 10, False)).items():
            rand[d].append(r)
    dates = sorted(d for d in mom10 if d in mom30 and rand.get(d))
    print(f"\n{name}: {' + '.join(s.label for s in specs)}, {len(symbols)} symbols ever, "
          f"{len(bars)} priced, {len(errors)} fetch errors; {len(dates)} books "
          f"{dates[0]}..{dates[-1]}")
    out = {}
    print(f"   {'arm':<9}{'mean/hold':>10}{'mom-rand':>10}{'NW t':>7}{'total':>9}{'max DD':>8}{'worst':>8}{'invested':>10}")
    for arm in ("base", "A trend", "B vol", "C broad"):
        src = mom30 if arm == "C broad" else mom10
        w = {d: exposure(bench, d, arm) for d in dates}
        mom = [src[d] * w[d] for d in dates]
        gap = [src[d] * w[d] - statistics.fmean(rand[d]) * w[d] for d in dates]
        total, dd, worst = account(mom)
        t = newey_west_t(gap, 2)
        out[arm] = {"gap": statistics.fmean(gap), "t": t, "dd": dd}
        print(f"   {arm:<9}{statistics.fmean(mom):>+9.2f}%{statistics.fmean(gap):>+9.2f}pp{t:>7.2f}"
              f"{total:>+8.0f}%{dd:>7.1f}%{worst:>+7.1f}%{statistics.fmean(w.values()) * 100:>9.0f}%")
    return out


async def main() -> int:
    async with httpx.AsyncClient(timeout=60) as client:
        results = {name: await run_dataset(name, specs, lo, hi, rng, client)
                   for name, specs, lo, hi, rng in DATASETS}
    print(f"\nPASS = CRASH max DD < {MAX_DD:.0f}%  AND  CRASH mom-random >= 0  AND  "
          f"LIVE mom-random > 0 with NW t >= {MIN_T}")
    for arm in ("base", "A trend", "B vol", "C broad"):
        c, lv = results["CRASH"][arm], results["LIVE"][arm]
        checks = (c["dd"] < MAX_DD, c["gap"] >= 0, lv["gap"] > 0 and lv["t"] >= MIN_T)
        print(f"   {arm:<9} DD {'ok' if checks[0] else 'X '}  crash edge {'ok' if checks[1] else 'X '}  "
              f"live edge {'ok' if checks[2] else 'X '}  ->  {'PASS' if all(checks) else 'fail'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
