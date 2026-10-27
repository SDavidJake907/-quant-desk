"""Does the momentum factor survive overlap-correct errors and costs? WORKLIST §5.

    python scripts/momentum_hac_test.py
    python scripts/momentum_hac_test.py --range 10y

READ-ONLY research. Changes nothing live.

THE TWO OPEN ITEMS
1. A 63-day hold rebalanced every 21 days: consecutive period means share
   two-thirds of their days, so they are autocorrelated and the engine's
   clustered t (which treats them as independent) is overstated. Newey-West
   with lag >= horizon/step - 1 = 2 corrects it; lag 3 and 5 are shown too.
2. No costs. Each trade is a round trip; it is charged here at 10, 25 and 50
   bps against its excess return, bracketing large-cap reality.

The run is backtest.py's own -- same point-in-time universe, recycled tickers
dropped, same engine and screen -- so the gross number is the one it prints.
Also run on the random control, the screen's own baseline.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from datetime import date

from scanner.backtest import engine
from scanner.features.bars import fetch_bars, fetch_many, range_start

from backtest import BENCHMARK, SCREENS, _memoized_membership, _point_in_time_universe

COSTS_BPS = (0, 10, 25, 50)
LAGS = (2, 3, 5)


def newey_west_t(series: list[float], lag: int) -> float:
    """t of the mean with Bartlett-weighted autocovariances up to `lag`."""
    n = len(series)
    if n < lag + 2:
        return 0.0
    mean = statistics.fmean(series)
    dev = [x - mean for x in series]
    var = sum(d * d for d in dev) / n
    for k in range(1, lag + 1):
        cov = sum(dev[i] * dev[i - k] for i in range(k, n)) / n
        var += 2 * (1 - k / (lag + 1)) * cov
    se = (max(var, 0.0) / n) ** 0.5
    return mean / se if se else 0.0


def periods(result: engine.BacktestResult, cost: float) -> list[float]:
    """Per-rebalance mean excess, net of `cost` per trade, in date order."""
    by: dict = {}
    for t in result.trades:
        by.setdefault(t.entry_date, []).append(t.excess_return - cost)
    return [statistics.fmean(by[d]) for d in sorted(by)]


def report(name: str, result: engine.BacktestResult, horizon: int) -> None:
    print(f"\n{name.upper()}  {result.count} trades, {result.effective_n} rebalances, "
          f"{horizon}d hold")
    print(f"  {'cost':>8}{'mean excess':>13}{'per year*':>11}{'clustered t':>13}"
          + "".join(f"{'NW t lag ' + str(lag):>12}" for lag in LAGS))
    for bps in COSTS_BPS:
        series = periods(result, bps / 100.0)   # excess is in percent
        mean = statistics.fmean(series)
        sd = statistics.stdev(series)
        naive = mean / (sd / len(series) ** 0.5) if sd else 0.0
        print(f"  {bps:>5}bps{mean:>+12.2f}%{mean * 252 / horizon:>+10.1f}%{naive:>13.2f}"
              + "".join(f"{newey_west_t(series, lag):>12.2f}" for lag in LAGS))
    print("  * per year = mean excess x (252 / hold); overlapping books, so this is "
          "the return of the strategy's average position, not a compounded account")


async def main(horizon: int, step: int, top: int, range_: str) -> int:
    random.seed(42)
    async with httpx.AsyncClient() as client:
        symbols, history = await _point_in_time_universe(client, range_)
        benchmark = await fetch_bars(BENCHMARK, client, range_=range_)
        bars, _ = await fetch_many(symbols, client, range_=range_)
    bars, _ = engine.drop_recycled(bars, history.exit_dates(range_start(range_), date.today()))
    membership = _memoized_membership(history)
    for name in ("momentum", "random"):
        result = engine.run(bars, benchmark, SCREENS[name], horizon_days=horizon,
                            step_days=step, top_n=top, require_all=(name != "random"),
                            membership=membership)
        report(name, result, horizon)
    print(f"\nNewey-West lag must be >= horizon/step - 1 = {horizon // step - 1}. "
          f"|t| > 2 is the usual bar; this is one strategy among many tried here.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--horizon", type=int, default=63)
    p.add_argument("--step", type=int, default=21)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--range", dest="range_", default="5y")
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.horizon, a.step, a.top, a.range_)))
