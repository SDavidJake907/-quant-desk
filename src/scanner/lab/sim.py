"""Run a recipe as a whole book over a date window, and score it.

THE BOOK: `top_n` equal slots. Every `rebalance_days` trading days, on the
close, the recipe fills its EMPTY slots with the best-ranked members that pass
its filters (the index's point-in-time membership; the sector cap counts held
names). The buy is at the NEXT day's close (Gemma's choice, 2026-10-23: no
same-day fill, no open-auction slippage). A slot is 1/top_n of the equity at
that moment; cash earns nothing.

EXITS, checked from the day after the buy: a bar that OPENS through the stop
fills at the open; one that trades through it fills at the stop; the target
the same way; a bar touching both is a STOP (the order inside a daily bar is
unknown, so it is settled against the trade). The time limit sells at the
close. Costs (backtest.costs, 5 bp spread + 2 bp slippage) on every fill.

THE RANDOM CONTROL: the same book, slots, dates and exits, filled with random
members instead of the recipe's picks. A recipe that cannot beat it is the
exits and the market talking, not the idea.

PURE: data in, numbers out.
"""

from __future__ import annotations

import random as _random
import statistics
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import NamedTuple

from scanner.backtest.costs import CostModel
from scanner.lab.recipe import FLAGS, Recipe
from scanner.models import Frozen

COSTS = CostModel()
RANDOM_SEEDS = 5
#: Gemma's bar (2026-10-23), for EACH hidden stage: beat its index by 15 points a
#: year, Sharpe above 1.2, worst drawdown 20% or less. Ours on top: beat the random
#: control, and at least MIN_TRADES trades (fewer cannot show anything).
BAR_EXCESS_PTS = 15.0
BAR_SHARPE = 1.2
BAR_MAX_DRAWDOWN = 20.0
MIN_TRADES = 20


class Stock(NamedTuple):
    symbol: str
    sector: str
    dates: Sequence[date]
    opens: Sequence[float]
    highs: Sequence[float]
    lows: Sequence[float]
    closes: Sequence[float]
    ind: Mapping[str, Sequence[float | None]]
    at: Mapping[date, int]


class Result(Frozen):
    start: date
    end: date
    annual_pct: float
    spx_annual_pct: float
    sharpe: float
    max_drawdown_pct: float
    trades: int
    win_rate_pct: float
    avg_trade_pct: float
    exposure_pct: float
    random_annual_pct: float | None = None

    @property
    def excess_pts(self) -> float:
        return self.annual_pct - self.spx_annual_pct

    def line(self) -> str:
        rnd = (f", random picks {self.random_annual_pct:+.1f}%/yr (you {self.annual_pct - self.random_annual_pct:+.1f} "
               "pts vs random)" if self.random_annual_pct is not None else "")
        return (f"{self.annual_pct:+.1f}%/yr vs the index {self.spx_annual_pct:+.1f}%/yr (you {self.excess_pts:+.1f} "
                f"pts vs the index){rnd}; Sharpe "
                f"{self.sharpe:.2f}, worst drawdown {self.max_drawdown_pct:.0f}%, {self.trades} trades, "
                f"{self.win_rate_pct:.0f}% won, avg trade {self.avg_trade_pct:+.2f}%, "
                f"invested {self.exposure_pct:.0f}% of the time")


def passes(r: Result) -> tuple[bool, str]:
    """Gemma's promotion bar plus the random control and a minimum sample."""
    misses = []
    if r.excess_pts < BAR_EXCESS_PTS:
        misses.append(f"beat the index by {r.excess_pts:+.1f} pts/yr (needs +{BAR_EXCESS_PTS:g})")
    if r.sharpe <= BAR_SHARPE:
        misses.append(f"Sharpe {r.sharpe:.2f} (needs > {BAR_SHARPE:g})")
    if r.max_drawdown_pct > BAR_MAX_DRAWDOWN:
        misses.append(f"drawdown {r.max_drawdown_pct:.0f}% (needs <= {BAR_MAX_DRAWDOWN:g}%)")
    if r.random_annual_pct is not None and r.annual_pct <= r.random_annual_pct:
        misses.append("did not beat random picks")
    if r.trades < MIN_TRADES:
        misses.append(f"{r.trades} trades (needs {MIN_TRADES})")
    return (not misses, "passed" if not misses else "; ".join(misses))


def _has(v: float | None) -> bool:
    """A usable indicator value: not missing (None in lists, NaN in compact arrays)."""
    return v is not None and v == v


def _ok(stock: Stock, i: int, recipe: Recipe, uptrend: bool) -> bool:
    for f in recipe.filters:
        if f.name == "market_uptrend":
            if not uptrend:
                return False
            continue
        v = stock.ind[f.name][i]
        if not _has(v):
            return False
        if f.name in FLAGS:
            if v < 0.5:
                return False
        elif ((f.op == "above" and v <= f.value) or (f.op == "below" and v >= f.value)
              or (f.op == "is" and v != f.value)):
            return False
    return _has(stock.ind[recipe.rank_by][i]) and _has(stock.ind["atr"][i])


class _Pos(NamedTuple):
    stock: Stock
    shares: float
    cost: float
    stop: float | None
    target: float | None
    held: int


def _exit(p: _Pos, i: int) -> float | None:
    s = p.stock
    if p.stop is not None:
        if s.opens[i] <= p.stop:
            return s.opens[i]
        if s.lows[i] <= p.stop:
            return p.stop
    if p.target is not None:
        if s.opens[i] >= p.target:
            return s.opens[i]
        if s.highs[i] >= p.target:
            return p.target
    return None


def simulate(recipe: Recipe, stocks: Mapping[str, Stock], calendar: Sequence[date],
             spx_closes: Mapping[date, float], members: Callable[[date], frozenset[str]],
             uptrend: Mapping[date, float], *, start: date, end: date,
             rng: _random.Random | None = None) -> Result:
    """One run. With `rng` the picks are random members (the control)."""
    days = [d for d in calendar if start <= d <= end]
    cash, held, pending = 1.0, [], []
    curve, invested, trades = [], [], []
    for k, d in enumerate(days):
        still = []
        for p in held:                                   # exits on today's bar
            i = p.stock.at.get(d)
            if i is None:
                still.append(p)
                continue
            fill = _exit(p, i)
            if fill is None and p.held + 1 >= recipe.max_hold_days:
                fill = p.stock.closes[i]
            if fill is None:
                still.append(p._replace(held=p.held + 1))
                continue
            got = p.shares * COSTS.sell_price(fill)
            cash += got
            trades.append(got / p.cost - 1)
        held = still
        equity = cash + sum(p.shares * p.stock.closes[p.stock.at[d]] if d in p.stock.at else p.cost
                            for p in held)
        for stock in pending:                            # yesterday's decisions fill at today's close
            i = stock.at.get(d)
            if i is None or len(held) >= recipe.top_n:
                continue
            spend = min(cash, equity / recipe.top_n)
            price = COSTS.buy_price(stock.closes[i])
            if spend <= 0 or price <= 0:
                continue
            atr = stock.ind["atr"][i] if _has(stock.ind["atr"][i]) else 0.0
            stop = stock.closes[i] - recipe.stop_atr * atr if recipe.stop_atr and atr else None
            target = (stock.closes[i] + recipe.target_r * recipe.stop_atr * atr
                      if recipe.target_r and stop is not None else None)
            held.append(_Pos(stock, spend / price, spend, stop, target, 0))
            cash -= spend
        pending = []
        value = cash + sum(p.shares * p.stock.closes[p.stock.at[d]] if d in p.stock.at else p.cost
                           for p in held)
        curve.append(value)
        invested.append(1 - cash / value if value > 0 else 0.0)
        free = recipe.top_n - len(held)
        if k % recipe.rebalance_days or free <= 0 or k + 1 >= len(days):
            continue
        owned = {p.stock.symbol for p in held}
        pool = [stocks[s] for s in sorted(members(d)) if s in stocks and s not in owned]
        up = bool(uptrend.get(d))
        if rng is not None:
            ready = [s for s in pool if (i := s.at.get(d)) is not None and _has(s.ind["atr"][i])]
            pending = rng.sample(ready, min(free, len(ready)))
            continue
        ready = [(s, s.ind[recipe.rank_by][s.at[d]]) for s in pool
                 if d in s.at and _ok(s, s.at[d], recipe, up)]
        ready.sort(key=lambda x: (x[1] if recipe.rank_order == "low_first" else -x[1], x[0].symbol))
        sectors: dict[str, int] = {}
        for p in held:
            sectors[p.stock.sector] = sectors.get(p.stock.sector, 0) + 1
        for s, _ in ready:
            if len(pending) >= free:
                break
            cap = recipe.max_per_sector
            if cap and s.sector != "unknown" and sectors.get(s.sector, 0) >= cap:
                continue
            sectors[s.sector] = sectors.get(s.sector, 0) + 1
            pending.append(s)
    return _score(curve, invested, trades, days, spx_closes)


def _score(curve, invested, trades, days, spx_closes) -> Result:
    years = max(len(curve) / 252, 1e-9)
    final = curve[-1] if curve else 1.0
    daily = [b / a - 1 for a, b in zip(curve, curve[1:]) if a > 0]
    sd = statistics.pstdev(daily) if len(daily) > 1 else 0.0
    peak, worst = 0.0, 0.0
    for v in curve:
        peak = max(peak, v)
        worst = max(worst, (1 - v / peak) if peak else 0.0)
    marks = [spx_closes[d] for d in days if d in spx_closes]
    spx = (marks[-1] / marks[0]) ** (1 / years) - 1 if len(marks) > 1 else 0.0
    return Result(start=days[0], end=days[-1], annual_pct=round((final ** (1 / years) - 1) * 100, 2),
                  spx_annual_pct=round(spx * 100, 2),
                  sharpe=round(statistics.fmean(daily) / sd * 252 ** 0.5, 2) if sd else 0.0,
                  max_drawdown_pct=round(worst * 100, 1), trades=len(trades),
                  win_rate_pct=round(sum(t > 0 for t in trades) / len(trades) * 100, 1) if trades else 0.0,
                  avg_trade_pct=round(statistics.fmean(trades) * 100, 2) if trades else 0.0,
                  exposure_pct=round(statistics.fmean(invested) * 100, 1) if invested else 0.0)


def with_control(recipe: Recipe, *args, **kwargs) -> Result:
    """The recipe's run, with the random control's mean annual return attached."""
    mine = simulate(recipe, *args, **kwargs)
    controls = [simulate(recipe, *args, **kwargs, rng=_random.Random(seed)).annual_pct
                for seed in range(RANDOM_SEEDS)]
    return mine.model_copy(update={"random_annual_pct": round(statistics.fmean(controls), 2)})
