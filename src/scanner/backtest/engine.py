"""Point-in-time backtest harness.

WHAT IT DOES
At each rebalance date it rebuilds every feature using ONLY bars up to that
date, runs the screen, takes the top N, and measures what happened next
against the benchmark over the same window.

DELIBERATE CONSERVATISM
Entry is the close of the day AFTER the screen date. You cannot screen on
Tuesday's close and buy at Tuesday's close — that one-bar shift is the single
most common way backtests lie.

KNOWN BIASES THAT REMAIN — read before trusting any number this produces:

  1. SURVIVORSHIP BIAS. Half-fixed, and you must know which half.

     Pass `membership=` (from `scanner.sources.index_history`) and the screen
     only sees companies that were actually in the index on that date. That
     removes the INCLUSION half of the bias, which is not the small half:
     without it a 2021 rebalance can buy a company added to the index in 2025,
     and index addition is itself a momentum event.

     The DELETION half survives, because it is a price-data problem, not a
     membership one. Yahoo returns 404 for delisted tickers, so companies that
     collapsed out of the index have no bars to trade even when membership
     says they belong. `BacktestResult.missing_members` counts them — and
     since deletions skew toward failures, treat a large count as "these
     results are still flattered", not as noise.

  2. OVERLAPPING WINDOWS. Rebalancing more often than the holding period means
     observations share days and are autocorrelated. Naive significance tests
     will overstate confidence.

  3. NO COSTS. No commission, no spread, no slippage, no market impact, no
     borrow. Real execution is worse than this, always.

  4. ONE REGIME. Two to five years of data covers one market environment. A
     strategy that worked 2024-2026 has not been tested against 2008 or 2000.

Treat the output as "is this idea worth more work", never as an expected return.
"""

from __future__ import annotations

import statistics
from datetime import date
from typing import Callable

from scanner.features.bars import Bars
from scanner.features.technicals import Features, compute, relative_strength
from scanner.models import Frozen
from scanner.screens.trend import Candidate, rank

ScreenFn = Callable[[Features, float | None], Candidate]

#: Index membership as of a date. See `scanner.sources.index_history`.
MembershipFn = Callable[[date], frozenset[str]]

MIN_BARS = 273  # 252 for 12-1 momentum + 21 for the skipped month


class Trade(Frozen):
    """One simulated position."""

    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    benchmark_return: float

    @property
    def trade_return(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return ((self.exit_price - self.entry_price) / self.entry_price) * 100.0

    @property
    def excess_return(self) -> float:
        """The only number that matters — beating the index, not just rising."""
        return self.trade_return - self.benchmark_return


class BacktestResult(Frozen):
    trades: tuple[Trade, ...]
    rebalances: int
    horizon_days: int
    top_n: int

    #: Symbols the screen actually ranked, per rebalance.
    universe_sizes: tuple[int, ...] = ()
    #: Index members with no usable price history — the unfixed half of
    #: survivorship bias, made countable instead of invisible.
    missing_members: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.trades)

    @property
    def mean_universe(self) -> float:
        return statistics.fmean(self.universe_sizes) if self.universe_sizes else 0.0

    @property
    def mean_excess(self) -> float:
        return statistics.fmean(t.excess_return for t in self.trades) if self.trades else 0.0

    @property
    def median_excess(self) -> float:
        return statistics.median(t.excess_return for t in self.trades) if self.trades else 0.0

    @property
    def hit_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.excess_return > 0)
        return (wins / len(self.trades)) * 100.0

    @property
    def mean_return(self) -> float:
        return statistics.fmean(t.trade_return for t in self.trades) if self.trades else 0.0

    @property
    def mean_benchmark(self) -> float:
        return statistics.fmean(t.benchmark_return for t in self.trades) if self.trades else 0.0

    @property
    def stdev_excess(self) -> float:
        if len(self.trades) < 2:
            return 0.0
        return statistics.stdev(t.excess_return for t in self.trades)

    @property
    def worst(self) -> float:
        return min((t.excess_return for t in self.trades), default=0.0)

    @property
    def best(self) -> float:
        return max((t.excess_return for t in self.trades), default=0.0)

    # --- clustered statistics -------------------------------------------
    #
    # Ten stocks held over the same 21 days are ONE bet on that window, not
    # ten independent bets. Treating them as independent shrinks the standard
    # error by sqrt(10) and manufactures significance out of nothing. The
    # honest unit of observation is the rebalance period.

    @property
    def period_excess(self) -> tuple[float, ...]:
        """Mean excess return per rebalance period."""
        by_period: dict[date, list[float]] = {}
        for trade in self.trades:
            by_period.setdefault(trade.entry_date, []).append(trade.excess_return)
        return tuple(statistics.fmean(v) for v in by_period.values())

    @property
    def effective_n(self) -> int:
        """Independent observations — periods, not trades."""
        return len(self.period_excess)

    @property
    def naive_t(self) -> float:
        """t-statistic treating every trade as independent. Overstated."""
        if len(self.trades) < 2 or self.stdev_excess == 0:
            return 0.0
        return self.mean_excess / (self.stdev_excess / (len(self.trades) ** 0.5))

    @property
    def clustered_t(self) -> float:
        """t-statistic on period means. The one to actually look at."""
        periods = self.period_excess
        if len(periods) < 2:
            return 0.0
        spread = statistics.stdev(periods)
        if spread == 0:
            return 0.0
        return statistics.fmean(periods) / (spread / (len(periods) ** 0.5))

    @property
    def period_hit_rate(self) -> float:
        """Share of rebalance periods that beat the benchmark."""
        periods = self.period_excess
        if not periods:
            return 0.0
        return (sum(1 for p in periods if p > 0) / len(periods)) * 100.0


def drop_recycled(
    bars_by_symbol: dict[str, Bars], exit_dates: dict[str, date]
) -> tuple[dict[str, Bars], tuple[str, ...]]:
    """Remove symbols whose price history starts after they left the index.

    A ticker freed up by a dead company gets reassigned, and the price feed
    happily serves the new owner's bars under the old symbol. History that
    begins after the original company was deleted cannot be that company's,
    so the series is a different business wearing its name.

    Returns (kept, dropped). Dropped names belong in the missing-coverage
    count, not silently in the universe.
    """
    kept: dict[str, Bars] = {}
    dropped: list[str] = []
    for symbol, bars in bars_by_symbol.items():
        gone_on = exit_dates.get(symbol)
        if gone_on is not None and bars.dates and bars.dates[0] > gone_on:
            dropped.append(symbol)
            continue
        kept[symbol] = bars
    return kept, tuple(sorted(dropped))


def _forward_return(bars: Bars, entry_i: int, exit_i: int) -> float | None:
    if entry_i >= len(bars) or exit_i >= len(bars):
        return None
    entry, exit_price = bars.closes[entry_i], bars.closes[exit_i]
    if entry == 0:
        return None
    return ((exit_price - entry) / entry) * 100.0


def run(
    bars_by_symbol: dict[str, Bars],
    benchmark: Bars,
    screen: ScreenFn,
    *,
    horizon_days: int = 21,
    step_days: int = 21,
    top_n: int = 10,
    require_all: bool = True,
    membership: MembershipFn | None = None,
) -> BacktestResult:
    """Walk forward through history, screening and measuring at each step.

    `membership` maps a date to the symbols in the index on that date. Omit it
    and every symbol in `bars_by_symbol` is eligible on every date — which
    lets the screen buy companies the index had not yet added. See the module
    docstring before trusting a run without it.
    """
    trades: list[Trade] = []
    rebalances = 0
    universe_sizes: list[int] = []
    missing: set[str] = set()

    # Benchmark dates drive the calendar so every symbol is evaluated on the
    # same trading days.
    first = MIN_BARS
    last = len(benchmark) - horizon_days - 2

    for bench_i in range(first, last, step_days):
        screen_date = benchmark.dates[bench_i]
        bench_entry, bench_exit = bench_i + 1, bench_i + 1 + horizon_days
        bench_fwd = _forward_return(benchmark, bench_entry, bench_exit)
        if bench_fwd is None:
            continue

        bench_features = compute(benchmark.upto(bench_i))
        rebalances += 1

        eligible = membership(screen_date) if membership is not None else None
        if eligible is not None:
            missing |= {s for s in eligible if s not in bars_by_symbol}

        scored: list[tuple[Candidate, Bars, int]] = []
        for symbol, bars in bars_by_symbol.items():
            if eligible is not None and symbol not in eligible:
                continue
            i = bars.index_of(screen_date)
            if i is None or i < MIN_BARS:
                continue
            # Point-in-time: nothing after `i` is visible here.
            features = compute(bars.upto(i))
            candidate = screen(features, relative_strength(features, bench_features))
            scored.append((candidate, bars, i))

        universe_sizes.append(len(scored))
        selected = rank(
            tuple(c for c, _, _ in scored), require_all=require_all
        )[:top_n]
        chosen = {c.symbol for c in selected}

        for candidate, bars, i in scored:
            if candidate.symbol not in chosen:
                continue
            entry_i, exit_i = i + 1, i + 1 + horizon_days
            if exit_i >= len(bars):
                continue
            trades.append(
                Trade(
                    symbol=candidate.symbol,
                    entry_date=bars.dates[entry_i],
                    exit_date=bars.dates[exit_i],
                    entry_price=bars.closes[entry_i],
                    exit_price=bars.closes[exit_i],
                    benchmark_return=bench_fwd,
                )
            )

    return BacktestResult(
        trades=tuple(trades),
        rebalances=rebalances,
        horizon_days=horizon_days,
        top_n=top_n,
        universe_sizes=tuple(universe_sizes),
        missing_members=tuple(sorted(missing)),
    )
