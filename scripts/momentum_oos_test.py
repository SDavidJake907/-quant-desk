"""Momentum on stocks it was never tested on: S&P MidCap 400 and SmallCap 600.

    python scripts/momentum_oos_test.py

READ-ONLY research. Changes nothing live.

WHY
The S&P 500 result (+2.68% excess per 63-day hold, Newey-West t 1.89, 10y,
WORKLIST §5) has been looked at, so it cannot confirm itself. If momentum is a
real effect and not a fit to 500 names, the SAME rules should earn on 1,000
other names over the same years. Published momentum is, if anything, stronger
outside large caps.

RULES -- FIXED 2026-10-05, COMMITTED BEFORE THIS SCRIPT RAN. Identical to the
S&P 500 run; nothing re-tuned for the new universe:
    universe     point-in-time members of the index on each rebalance date
                 (index_history, share classes collapsed)
    signal       12-1 month return (screens/momentum_factor.py)
    book         top 10, equal weight, held 63 trading days, a new book every
                 21 days (overlapping tranches), 10 years of daily bars
    benchmark    the index itself (^SP400 / ^SP600)
    control      the same machinery picking 10 at random, mean of 5 seeds
    costs        each hold charged 25bps and 50bps round trip
PASS (decided now): in BOTH indexes, momentum's mean excess per hold after
25bps is positive AND above the random control's. STRONG PASS: pooled
Newey-West t (lag 2) >= 2 after 25bps. Anything less is a fail for window 16.
Also shown, not scored: 2016-2020 vs 2021-2026 halves. Membership is only
verified to within 2% back to 2021 (index_history); the early half is weaker.

STILL BIASED UP: Yahoo serves no history for delisted names; their count is
printed. A fail here is a strong fail; a pass is a lead for the dry run.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
from datetime import date

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.backtest import engine
from scanner.features.bars import fetch_bars, fetch_many, range_start
from scanner.sources import index_history
from scanner.sources.index_history import SP400, SP600

sys.path.insert(0, "scripts")
from backtest import SCREENS  # noqa: E402
from momentum_hac_test import newey_west_t, periods  # noqa: E402

HORIZON, STEP, TOP, RANGE = 63, 21, 10, "10y"
COSTS_BPS = (25, 50)
SEEDS = (1, 2, 3, 4, 5)
SPLIT = date(2021, 1, 1)
INDEXES = ((SP400, "^SP400"), (SP600, "^SP600"))


def membership_fn(history):
    cache: dict[date, frozenset[str]] = {}

    def members_on(on: date) -> frozenset[str]:
        if on not in cache:
            cache[on] = history.members_on(on, collapse_share_classes=True)
        return cache[on]
    return members_on


def per_hold(result: engine.BacktestResult, bps: int, lo=None, hi=None) -> list[float]:
    """Per-rebalance mean excess (%), net of costs, optionally within dates."""
    trades = [t for t in result.trades
              if (lo is None or t.entry_date >= lo) and (hi is None or t.entry_date < hi)]
    sub = result.model_copy(update={"trades": tuple(trades)})
    return periods(sub, bps / 100.0) if trades else []


def mean(xs: list[float]) -> float:
    return statistics.fmean(xs) if xs else float("nan")


async def run_index(spec, bench_symbol: str, client) -> dict:
    history = await index_history.load(client, spec=spec)
    start = range_start(RANGE)
    symbols = tuple(sorted(history.symbols_ever(start, date.today(), collapse_share_classes=True)))
    benchmark = await fetch_bars(bench_symbol, client, range_=RANGE)
    bars, errors = await fetch_many(symbols, client, range_=RANGE)
    bars, _ = engine.drop_recycled(bars, history.exit_dates(start, date.today()))
    members = membership_fn(history)
    momentum = engine.run(bars, benchmark, SCREENS["momentum"], horizon_days=HORIZON,
                          step_days=STEP, top_n=TOP, membership=members)
    randoms = []
    for seed in SEEDS:
        random.seed(seed)
        randoms.append(engine.run(bars, benchmark, SCREENS["random"], horizon_days=HORIZON,
                                  step_days=STEP, top_n=TOP, require_all=False, membership=members))
    print(f"\n{spec.label}: {len(symbols)} symbols ever in the index since {start}, "
          f"{len(bars)} with bars, {len(errors)} fetch errors, "
          f"{len(momentum.missing_members)} members never priced (delisted)")
    print(f"   {momentum.count} momentum trades over {momentum.effective_n} rebalances")
    out = {"momentum": momentum, "random": randoms}
    for bps in COSTS_BPS:
        m = per_hold(momentum, bps)
        r = mean([mean(per_hold(x, bps)) for x in randoms])
        print(f"   {bps}bps  momentum {mean(m):+6.2f}% excess/hold  NW t {newey_west_t(m, 2):5.2f}"
              f"   random control {r:+6.2f}%   momentum - random {mean(m) - r:+6.2f}pp")
    m_a, m_b = per_hold(momentum, 25, hi=SPLIT), per_hold(momentum, 25, lo=SPLIT)
    r_a = mean([mean(per_hold(x, 25, hi=SPLIT)) for x in randoms])
    r_b = mean([mean(per_hold(x, 25, lo=SPLIT)) for x in randoms])
    print(f"   halves @25bps   before 2021: {mean(m_a):+6.2f}% (random {r_a:+6.2f}%)   "
          f"2021 on: {mean(m_b):+6.2f}% (random {r_b:+6.2f}%)")
    out["m25"] = per_hold(momentum, 25)
    out["r25"] = mean([mean(per_hold(x, 25)) for x in randoms])
    return out


async def main() -> int:
    print(f"momentum out of sample: top {TOP}, {HORIZON}d hold, {STEP}d step, {RANGE}, "
          f"point-in-time membership")
    async with httpx.AsyncClient(timeout=60) as client:
        results = [await run_index(spec, bench, client) for spec, bench in INDEXES]
    passes = [mean(r["m25"]) > 0 and mean(r["m25"]) > r["r25"] for r in results]
    pooled = [x for r in results for x in r["m25"]]
    t = newey_west_t(pooled, 2)
    print(f"\nPASS RULE (25bps): positive and above random in both indexes -> "
          f"{'MidCap ' + ('yes' if passes[0] else 'NO')}, {'SmallCap ' + ('yes' if passes[1] else 'NO')}")
    print(f"pooled Newey-West t (lag 2): {t:.2f}  (strong pass needs >= 2)")
    print("VERDICT: " + ("STRONG PASS" if all(passes) and t >= 2 else
                         "PASS" if all(passes) else "FAIL"))
    return 0


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    raise SystemExit(asyncio.run(main()))
