"""Backtest the momentum swing setup on daily bars.

    python scripts/momentum_backtest.py                       # default basket
    python scripts/momentum_backtest.py --index sp500 --limit 200
    python scripts/momentum_backtest.py --holdout 0.3         # train + holdout
    python scripts/momentum_backtest.py --atr-multiple 2 3    # sweep

THE QUESTION, WHICH IS NOT "IS IT PROFITABLE"
Every trade here has a stop, a target and a time limit, and that machinery
earns money on a rising market by itself. So the number that matters is the
gap between the pattern and IDENTICAL management entered at random. The coiled
screen died on exactly this test. The trend screen has never taken it.

WHY DAILY BARS CHANGE WHAT THIS CAN ANSWER
Every other backtest in this repo is capped at Yahoo's 60-day intraday window,
which is a single regime and roughly 200 trades. Daily bars reach back years
and across regimes, so for the first time a result here can be asked whether it
survives a bear market rather than merely a quarter.

WHAT THIS STILL CANNOT ANSWER
Survivorship. `--index` uses point-in-time membership for SELECTION, but Yahoo
serves no history for delisted names, so a company that left the index and then
failed is simply absent. That biases every long result upward and no flag here
fixes it.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics

import httpx

from scanner.backtest.costs import CostModel
from scanner.backtest.setups import (
    STOP,
    TARGET,
    TIMEOUT,
    Report,
    random_control,
    resolve,
)
from scanner.features.bars import fetch_bars
from scanner.screens import momentum
from scanner.sources import composite
from scanner.sources.index_history import SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

BENCHMARK = "^GSPC"

DEFAULT_BASKET = ("MU", "NVDA", "AMD", "AVGO", "TSM", "AAPL", "MSFT", "META",
                  "AMZN", "GOOGL", "TSLA", "NFLX", "CRM", "ORCL", "QCOM")
INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,)}

#: Trading days a swing trade may stay open. Sixty sessions is about a quarter
#: — long enough for a momentum move to work, short enough that a dead position
#: is not held forever. Not tuned; a starting point to be measured.
DEFAULT_MAX_DAYS = 60


def _report(label: str, report: Report, costs: CostModel) -> None:
    if not report.count:
        print(f"  {YELLOW}{label}: no resolved trades{RESET}")
        return
    net = report.net_expectancy(costs)
    sign = GREEN if report.expectancy > 0 else RED
    net_sign = GREEN if net > 0 else RED
    print(f"  {BOLD}{label:<8}{RESET} {report.count:5} trades   "
          f"win {report.win_rate:5.1f}%   "
          f"gross {sign}{report.expectancy:+.3f}R{RESET}   "
          f"net {net_sign}{net:+.3f}R{RESET}   total {report.total_r:+.0f}R")
    print(f"           {DIM}target {report.share(TARGET):4.1f}%  "
          f"stop {report.share(STOP):4.1f}%  timeout {report.share(TIMEOUT):4.1f}%   "
          f"stop distance {report.mean_risk_pct:.1f}%   "
          f"stdev {report.stdev_r:.2f}R   naive t {report.t_stat:+.2f}{RESET}")


async def collect(symbols: tuple[str, ...], range_: str):
    """Daily bars for every symbol, plus the benchmark.

    The benchmark is fetched over the same window and aligned by DATE inside
    the screen, so a name that listed late is still compared against the index
    on its own dates rather than by bar count.
    """
    async with httpx.AsyncClient() as client:
        benchmark = await fetch_bars(BENCHMARK, client, range_=range_)
        results = await asyncio.gather(
            *(fetch_bars(s, client, range_=range_) for s in symbols),
            return_exceptions=True,
        )
    bars_by_symbol, missing = {}, []
    for symbol, outcome in zip(symbols, results):
        if isinstance(outcome, Exception):
            missing.append(symbol)
        else:
            bars_by_symbol[symbol] = outcome
    return bars_by_symbol, benchmark, missing


def _hold_days(outcomes) -> float:
    if not outcomes:
        return 0.0
    return statistics.fmean(o.exit_index - o.entry_index for o in outcomes)


async def main(
    symbols: tuple[str, ...], atr_multiples: tuple[float, ...], reward_risk: float,
    range_: str, max_days: int, control_draws: int, costs: CostModel,
    holdout_fraction: float | None = None,
) -> None:
    print(f"{DIM}fetching {len(symbols)} symbols + {BENCHMARK}, daily bars, {range_}{RESET}")
    bars_by_symbol, benchmark, missing = await collect(symbols, range_)
    if not bars_by_symbol:
        raise SystemExit("no price history loaded")

    depth = min(len(b) for b in bars_by_symbol.values())
    print(f"{DIM}loaded {len(bars_by_symbol)} symbols ({len(missing)} unavailable), "
          f"shallowest series {depth} bars, benchmark {len(benchmark)} bars{RESET}")
    print(f"{DIM}entry: close of the signal bar | stop: N x ATR(14) | "
          f"target: {reward_risk}R | time limit: {max_days} sessions{RESET}")
    print(f"{DIM}costs: {costs.round_trip_bps:.1f}bps round trip{RESET}")

    for atr_multiple in atr_multiples:
        # Setups are found ONCE per symbol over the whole series. The split
        # below then selects which of them count, so a train/holdout boundary
        # never changes what the screen saw — only which trades are scored.
        setups_by_symbol = {
            symbol: momentum.detect(bars, benchmark, atr_multiple=atr_multiple,
                                    reward_risk=reward_risk)
            for symbol, bars in bars_by_symbol.items()
        }

        periods = (("full", 0.0, 1.0),)
        if holdout_fraction is not None:
            periods = (("train", 0.0, 1.0 - holdout_fraction),
                       ("holdout", 1.0 - holdout_fraction, 1.0))

        for label, lower, upper in periods:
            real, control = [], []
            rng = random.Random(42)
            with_setups = 0
            for symbol, bars in bars_by_symbol.items():
                setups = setups_by_symbol[symbol]
                if not setups:
                    continue
                start_index = int(len(bars) * lower)
                end_index = int(len(bars) * upper)
                in_window = tuple(
                    s for s in setups if start_index <= s.trigger_index < end_index
                )
                if not in_window:
                    continue
                with_setups += 1
                for setup in in_window:
                    outcome = resolve(
                        bars,
                        symbol=setup.symbol,
                        entry_index=setup.trigger_index,
                        entry_price=setup.entry_price,
                        stop_price=setup.stop_price,
                        target_price=setup.target_price,
                        max_bars=max_days,
                        end_index=end_index,
                    )
                    if outcome is not None:
                        real.append(outcome)
                # The control must draw from the SAME eligible bars as the
                # pattern. The screen cannot signal before it has a 200-day
                # average, so a control starting at bar 1 is being offered
                # entries the strategy could never have taken — a different
                # opportunity set, and the comparison stops being like-for-like.
                control.extend(random_control(
                    bars, in_window, rng, max_bars=max_days, draws=control_draws,
                    start_index=max(start_index, momentum.MIN_HISTORY - 1),
                    end_index=end_index,
                ))

            suffix = f" {label}" if holdout_fraction is not None else ""
            total_signals = sum(len(s) for s in setups_by_symbol.values())
            print(f"\n{BOLD}ATR x {atr_multiple:g}{suffix}{RESET}  "
                  f"{DIM}({with_setups} of {len(bars_by_symbol)} symbols signalled; "
                  f"{total_signals} signals over the full series){RESET}")

            pattern = Report(label="pattern", outcomes=tuple(real))
            rand = Report(label="random", outcomes=tuple(control))
            _report("pattern", pattern, costs)
            _report("random", rand, costs)
            if pattern.count:
                print(f"           {DIM}mean hold {_hold_days(real):.0f} sessions{RESET}")

            if pattern.count and rand.count:
                edge = pattern.net_expectancy(costs) - rand.net_expectancy(costs)
                verdict = (f"{GREEN}beats random by {edge:+.3f}R net{RESET}" if edge > 0
                           else f"{RED}loses to random by {edge:+.3f}R net{RESET}")
                print(f"  -> {verdict}")

    print(f"\n{YELLOW}Before this number is used for anything:{RESET}")
    print(f"{DIM}  - delisted names are absent from Yahoo, so losers are missing entirely{RESET}")
    print(f"{DIM}  - a bar touching stop and target both is scored as a stop, always{RESET}")
    print(f"{DIM}  - overlapping signals across symbols share market beta and are not independent{RESET}")
    print(f"{DIM}  - t treats trades as independent; read it as an upper bound{RESET}")
    print(f"{DIM}  - nothing here has been through a holdout unless --holdout was passed{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--index", choices=list(INDEXES), default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--atr-multiple", type=float, nargs="+",
                   default=[momentum.DEFAULT_ATR_MULTIPLE],
                   help="stop distance in ATRs; several values sweep")
    p.add_argument("--reward-risk", type=float, default=momentum.DEFAULT_REWARD_RISK)
    p.add_argument("--range", dest="range_", default="10y")
    p.add_argument("--max-days", type=int, default=DEFAULT_MAX_DAYS)
    p.add_argument("--control-draws", type=int, default=5)
    p.add_argument("--spread-bps", type=float, default=5.0)
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--holdout", type=float, default=None, metavar="FRACTION",
                   help="chronological holdout, e.g. 0.3 for the last 30%%")
    a = p.parse_args()

    async def _run() -> None:
        if a.symbols:
            symbols = tuple(a.symbols)
        elif a.index:
            async with httpx.AsyncClient() as client:
                universe = await composite.load_universe(client, INDEXES[a.index])
            symbols = tuple(m.symbol for m in universe)
        else:
            symbols = DEFAULT_BASKET
        if a.limit:
            symbols = symbols[:a.limit]
        await main(
            symbols, tuple(a.atr_multiple), a.reward_risk, a.range_, a.max_days,
            a.control_draws,
            CostModel(spread_bps=a.spread_bps, slippage_bps=a.slippage_bps),
            holdout_fraction=a.holdout,
        )

    asyncio.run(_run())
