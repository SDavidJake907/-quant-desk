"""Momentum on the S&P 500 in 2000-2015: the years the 10y run never saw,
including the dot-com bust and the 2009 momentum crash.

    python scripts/momentum_early_test.py

READ-ONLY research. Changes nothing live.

WHY
The 10y S&P 500 run (WORKLIST §5) and the 400/600 run (§31) both start in
2016 -- one long bull market with one fast crash. Momentum's known failure is
the sharp reversal after a bear market (2009: past losers rallied, past
winners lagged). Funding needs to know how deep that goes, because the stops
in GO_LIVE (3% day, 6% week, 15% drawdown) were written for the swing strategy.

DATA
S&P 500 point-in-time membership, verified within 2% of 500 names back to
2000 (membership_drift audit, 2026-10-05). Yahoo daily "max" history, cut to
1999-06 .. 2016-03, so books open roughly 2000-06 .. 2015-12. Delisted names
(Enron, Lehman, WorldCom...) have no bars and are counted, not fixed -- worse
here than in the 10y run, so the result reads HIGH, and the drawdowns LOW.

RULES -- FIXED 2026-10-05, COMMITTED BEFORE THIS SCRIPT RAN. Same as §5/§31:
top 10 by 12-1, equal weight, 63-day hold, a new book every 21 days, 25bps and
50bps per hold, vs ^GSPC and a 5-seed random control.
PASS (decided now): momentum minus random, per hold after 25bps, > 0 over the
whole period. Reported, not scored: by era (2000-02 bust, 2003-07, 2008-09,
2010-15), the worst single hold, and the max drawdown of the three-tranche
account (sampled every 21 days -- coarser than daily, so it UNDERSTATES).
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

sys.path.insert(0, "scripts")
from backtest import SCREENS  # noqa: E402
from momentum_hac_test import newey_west_t  # noqa: E402
from momentum_oos_test import membership_fn  # noqa: E402

HORIZON, STEP, TOP = 63, 21, 10
START, END = date(1999, 6, 1), date(2016, 3, 31)
SEEDS = (1, 2, 3, 4, 5)
COST_BPS = 25
ERAS = (("2000-02 bust", date(2000, 1, 1), date(2002, 12, 31)),
        ("2003-07", date(2003, 1, 1), date(2007, 12, 31)),
        ("2008-09", date(2008, 1, 1), date(2009, 12, 31)),
        ("2010-15", date(2010, 1, 1), date(2015, 12, 31)))


def window(bars: Bars) -> Bars | None:
    keep = [i for i, d in enumerate(bars.dates) if START <= d <= END]
    if not keep:
        return None
    a, b = keep[0], keep[-1] + 1
    return bars.model_copy(update={f: getattr(bars, f)[a:b] for f in
                                   ("dates", "opens", "closes", "volumes", "highs", "lows")})


def books(result: engine.BacktestResult) -> list[tuple[date, float, float]]:
    """Per rebalance: (date, mean trade return net of cost, benchmark return), %."""
    by = defaultdict(list)
    bench = {}
    for t in result.trades:
        by[t.entry_date].append(t.trade_return - COST_BPS / 100)
        bench[t.entry_date] = t.benchmark_return
    return [(d, statistics.fmean(by[d]), bench[d]) for d in sorted(by)]


def tranche_drawdown(series: list[float]) -> tuple[float, float]:
    """Three overlapping tranches, each compounding every third book. (total %, max DD %)."""
    equity = [1 / 3, 1 / 3, 1 / 3]
    peak, worst = 1.0, 0.0
    for i, r in enumerate(series):
        equity[i % 3] *= 1 + r / 100
        total = sum(equity)
        peak = max(peak, total)
        worst = max(worst, (peak - total) / peak)
    return (sum(equity) - 1) * 100, worst * 100


async def main() -> int:
    async with httpx.AsyncClient(timeout=60) as client:
        history = await index_history.load(client)
        symbols = tuple(sorted(history.symbols_ever(START, END, collapse_share_classes=True)))
        benchmark = window(await fetch_bars("^GSPC", client, range_="max"))
        raw, errors = await fetch_many(symbols, client, range_="max")
    bars = {s: w for s, b in raw.items() if (w := window(b)) is not None and len(w) > 300}
    bars, _ = engine.drop_recycled(bars, history.exit_dates(START, END))
    members = membership_fn(history)
    mom = engine.run(bars, benchmark, SCREENS["momentum"], horizon_days=HORIZON,
                     step_days=STEP, top_n=TOP, membership=members)
    rands = []
    for seed in SEEDS:
        random.seed(seed)
        rands.append(engine.run(bars, benchmark, SCREENS["random"], horizon_days=HORIZON,
                                step_days=STEP, top_n=TOP, require_all=False, membership=members))
    print(f"S&P 500 {START}..{END}: {len(symbols)} symbols ever in the index, {len(bars)} priced, "
          f"{len(errors)} fetch errors, {len(mom.missing_members)} members never priced")
    m = books(mom)
    r_by = defaultdict(list)
    for res in rands:
        for d, ret, _ in books(res):
            r_by[d].append(ret)
    gap = [(d, ret - statistics.fmean(r_by[d])) for d, ret, _ in m if r_by.get(d)]
    excess = [ret - b for _, ret, b in m]
    print(f"{len(m)} books {m[0][0]}..{m[-1][0]}, top {TOP}, {HORIZON}d hold, {COST_BPS}bps\n")
    print(f"   momentum excess over S&P   {statistics.fmean(excess):+6.2f}%/hold  NW t {newey_west_t(excess, 2):5.2f}")
    gaps = [g for _, g in gap]
    print(f"   momentum minus random      {statistics.fmean(gaps):+6.2f}pp/hold NW t {newey_west_t(gaps, 2):5.2f}")
    print("\n   by era            momentum  S&P     random   mom-random")
    for label, lo, hi in ERAS:
        part = [(d, ret, b) for d, ret, b in m if lo <= d <= hi]
        if not part:
            continue
        rnd = [statistics.fmean(r_by[d]) for d, _, _ in part if r_by.get(d)]
        print(f"   {label:<16}{statistics.fmean(x[1] for x in part):+7.2f}% {statistics.fmean(x[2] for x in part):+6.2f}% "
              f"{statistics.fmean(rnd):+7.2f}%  {statistics.fmean(x[1] for x in part) - statistics.fmean(rnd):+7.2f}pp")
    worst = min(m, key=lambda x: x[1])
    print(f"\n   worst single book: {worst[0]}  {worst[1]:+.1f}% (S&P {worst[2]:+.1f}%)")
    tot, dd = tranche_drawdown([ret for _, ret, _ in m])
    btot, bdd = tranche_drawdown([b for _, _, b in m])
    print(f"   three-tranche account: momentum {tot:+.0f}% total, max DD {dd:.1f}%   |   "
          f"S&P same sampling {btot:+.0f}%, max DD {bdd:.1f}%")
    print(f"\nPASS RULE: momentum minus random > 0 per hold after {COST_BPS}bps -> "
          f"{'PASS' if statistics.fmean(gaps) > 0 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
