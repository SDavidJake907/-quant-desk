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
unknown, so it is settled against the trade). At the close, in this order: the
trailing exit (a close more than trail_atr ATRs under the highest close), the sell
rules (any one true sells), the time limit. A buy at the NEXT OPEN (entry_at) fills at
the open; that day's range can still reach its levels. Costs (backtest.costs, 5 bp
spread + 2 bp slippage) on every fill.

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
#: Gemma's bar, revised by her 2026-10-23 ("a necessary pivot from 'impossible' to
#: 'professional'"; it was +15 pts / Sharpe 1.2 / drawdown 20%), for EACH hidden stage:
#: beat the index by BAR_EXCESS_PTS a year, a Sharpe above the index's own over the same
#: days, a worst drawdown no more than BAR_DD_OVER_INDEX points deeper than the index's,
#: beat the random control, at least MIN_TRADES trades.
BAR_EXCESS_PTS = 3.0
BAR_DD_OVER_INDEX = 5.0
#: Scoring version: 2 = idle cash earns the index (Gemma, 2026-10-23). Results of version 1
#: (idle cash earned nothing) are not comparable and are kept apart in her log.
SCORING = 2
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
    index_sharpe: float = 0.0
    index_drawdown_pct: float = 0.0

    @property
    def excess_pts(self) -> float:
        return self.annual_pct - self.spx_annual_pct

    def line(self) -> str:
        rnd = (f", random picks {self.random_annual_pct:+.1f}%/yr (you {self.annual_pct - self.random_annual_pct:+.1f} "
               "pts vs random)" if self.random_annual_pct is not None else "")
        return (f"{self.annual_pct:+.1f}%/yr vs the index {self.spx_annual_pct:+.1f}%/yr (you {self.excess_pts:+.1f} "
                f"pts vs the index){rnd}; Sharpe {self.sharpe:.2f} (index {self.index_sharpe:.2f}), "
                f"worst drawdown {self.max_drawdown_pct:.0f}% (index {self.index_drawdown_pct:.0f}%), {self.trades} trades, "
                f"{self.win_rate_pct:.0f}% won, avg trade {self.avg_trade_pct:+.2f}%, "
                f"invested {self.exposure_pct:.0f}% of the time")


def passes(r: Result) -> tuple[bool, str]:
    """Gemma's promotion bar plus the random control and a minimum sample."""
    misses = []
    if r.excess_pts < BAR_EXCESS_PTS:
        misses.append(f"beat the index by {r.excess_pts:+.1f} pts/yr (needs +{BAR_EXCESS_PTS:g})")
    if r.sharpe <= r.index_sharpe:
        misses.append(f"Sharpe {r.sharpe:.2f} (needs above the index's {r.index_sharpe:.2f})")
    if r.max_drawdown_pct > r.index_drawdown_pct + BAR_DD_OVER_INDEX:
        misses.append(f"drawdown {r.max_drawdown_pct:.0f}% (needs <= the index's "
                      f"{r.index_drawdown_pct:.0f}% + {BAR_DD_OVER_INDEX:g})")
    if r.random_annual_pct is not None and r.annual_pct <= r.random_annual_pct:
        misses.append("did not beat random picks")
    if r.trades < MIN_TRADES:
        misses.append(f"{r.trades} trades (needs {MIN_TRADES})")
    return (not misses, "passed" if not misses else "; ".join(misses))


def _has(v: float | None) -> bool:
    """A usable indicator value: not missing (None in lists, NaN in compact arrays)."""
    return v is not None and v == v


def _holds(stock: Stock, i: int, f, uptrend: bool) -> bool | None:
    """Whether one condition holds on bar i; None when its value is missing."""
    if f.name == "market_uptrend":
        v = 1.0 if uptrend else 0.0
    else:
        v = stock.ind[f.name][i]
        if not _has(v):
            return None
    if f.name in FLAGS:
        return (v >= 0.5) != (f.op == "not")
    return ((f.op == "above" and v > f.value) or (f.op == "below" and v < f.value)
            or (f.op == "is" and v == f.value))


def _ok(stock: Stock, i: int, recipe: Recipe, uptrend: bool) -> bool:
    """Every buy filter holds (a missing value is a miss)."""
    if not all(_holds(stock, i, f, uptrend) for f in recipe.filters):
        return False
    return _has(stock.ind[recipe.rank_by][i]) and _has(stock.ind["atr"][i])


def _sell_rule(stock: Stock, i: int, recipe: Recipe, uptrend: bool):
    """The first sell rule true at this close, or None (a missing value never sells)."""
    return next((f for f in recipe.exit_filters if _holds(stock, i, f, uptrend)), None)


class _Pos(NamedTuple):
    stock: Stock
    shares: float
    cost: float
    stop: float | None
    target: float | None
    held: int
    atr: float = 0.0
    high_close: float = 0.0
    #: Bought at today's open: today's bar after the open can still hit the levels,
    #: but the open itself was the fill, so no gap rule applies to it.
    at_open: bool = False
    #: The quoted buy price (before costs): profit locks and percent trails measure from it.
    entry: float = 0.0


def _exit(p: _Pos, i: int) -> float | None:
    s = p.stock
    if p.stop is not None:
        if not p.at_open and s.opens[i] <= p.stop:
            return s.opens[i]
        if s.lows[i] <= p.stop:
            return p.stop
    if p.target is not None:
        if not p.at_open and s.opens[i] >= p.target:
            return s.opens[i]
        if s.highs[i] >= p.target:
            return p.target
    return None


def _open(stock: Stock, i: int, price_now: float, spend: float, recipe: Recipe, at_open: bool) -> _Pos | None:
    price = COSTS.buy_price(price_now)
    if spend <= 0 or price <= 0:
        return None
    atr = stock.ind["atr"][i - 1 if at_open and i else i]       # at the open only yesterday's ATR is known
    atr = atr if _has(atr) else 0.0
    stop = price_now - recipe.stop_atr * atr if recipe.stop_atr and atr else None
    target = (price_now + recipe.target_r * recipe.stop_atr * atr
              if recipe.target_r and stop is not None else None)
    return _Pos(stock, spend / price, spend, stop, target, 0, atr, price_now, at_open, price_now)


def _value(cash: float, held, d) -> float:
    return cash + sum(p.shares * p.stock.closes[p.stock.at[d]] if d in p.stock.at else p.cost for p in held)


def simulate(recipe: Recipe, stocks: Mapping[str, Stock], calendar: Sequence[date],
             spx_closes: Mapping[date, float], members: Callable[[date], frozenset[str]],
             uptrend: Mapping[date, float], *, start: date, end: date,
             rng: _random.Random | None = None, idle_in_index: bool = True) -> Result:
    """One run. With `rng` the picks are random members (the control)."""
    days = [d for d in calendar if start <= d <= end]
    cash, held, pending = 1.0, [], []
    curve, invested, trades = [], [], []
    last_value = 1.0
    prev = None
    for k, d in enumerate(days):
        up = bool(uptrend.get(d))
        # Idle cash sits in the index (Gemma, 2026-10-23): it earns the index's day.
        # idle_in_index=False: idle money is plain cash (how the live book holds a sale) -- the fair
        # test for a market-timing exit, which cannot protect anything if its cash is the index.
        if idle_in_index and prev is not None and spx_closes.get(prev) and spx_closes.get(d):
            cash *= spx_closes[d] / spx_closes[prev]
        prev = d
        if recipe.entry_at == "next_open":               # yesterday's decisions fill at today's open
            for stock in pending:
                i = stock.at.get(d)
                if i is None or len(held) >= recipe.top_n:
                    continue
                pos = _open(stock, i, stock.opens[i], min(cash, last_value / recipe.top_n), recipe, True)
                if pos:
                    held.append(pos)
                    cash -= pos.cost
            pending = []
        still = []
        for p in held:                                   # exits on today's bar
            i = p.stock.at.get(d)
            if i is None:
                still.append(p)
                continue
            close = p.stock.closes[i]
            fill = _exit(p, i)
            if fill is None:
                high = max(p.high_close, close)
                best = high / p.entry - 1 if p.entry else 0.0
                if recipe.trail_atr and p.atr and close < high - recipe.trail_atr * p.atr:
                    fill = close
                elif recipe.trail_pct and close < high * (1 - recipe.trail_pct / 100):
                    fill = close
                elif (recipe.lock_at_pct and best * 100 >= recipe.lock_at_pct
                      and close < p.entry * (1 + recipe.lock_keep * best)):
                    fill = close
                elif _sell_rule(p.stock, i, recipe, up) is not None:
                    fill = close
                elif p.held + 1 >= recipe.max_hold_days:
                    fill = close
            if fill is None:
                still.append(p._replace(held=p.held + 1, high_close=max(p.high_close, close), at_open=False))
                continue
            got = p.shares * COSTS.sell_price(fill)
            cash += got
            trades.append(got / p.cost - 1)
        held = still
        if recipe.entry_at == "next_close":              # yesterday's decisions fill at today's close
            equity = _value(cash, held, d)
            for stock in pending:
                i = stock.at.get(d)
                if i is None or len(held) >= recipe.top_n:
                    continue
                pos = _open(stock, i, stock.closes[i], min(cash, equity / recipe.top_n), recipe, False)
                if pos:
                    held.append(pos)
                    cash -= pos.cost
            pending = []
        value = _value(cash, held, d)
        last_value = value
        curve.append(value)
        invested.append(1 - cash / value if value > 0 else 0.0)
        free = recipe.top_n - len(held)
        if k % recipe.rebalance_days or free <= 0 or k + 1 >= len(days):
            continue
        owned = {p.stock.symbol for p in held}
        pool = [stocks[s] for s in sorted(members(d)) if s in stocks and s not in owned]
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
    idx_daily = [b / a - 1 for a, b in zip(marks, marks[1:]) if a > 0]
    idx_sd = statistics.pstdev(idx_daily) if len(idx_daily) > 1 else 0.0
    ipeak, iworst = 0.0, 0.0
    for v in marks:
        ipeak = max(ipeak, v)
        iworst = max(iworst, (1 - v / ipeak) if ipeak else 0.0)
    return Result(start=days[0], end=days[-1], annual_pct=round((final ** (1 / years) - 1) * 100, 2),
                  spx_annual_pct=round(spx * 100, 2),
                  sharpe=round(statistics.fmean(daily) / sd * 252 ** 0.5, 2) if sd else 0.0,
                  max_drawdown_pct=round(worst * 100, 1), trades=len(trades),
                  win_rate_pct=round(sum(t > 0 for t in trades) / len(trades) * 100, 1) if trades else 0.0,
                  avg_trade_pct=round(statistics.fmean(trades) * 100, 2) if trades else 0.0,
                  exposure_pct=round(statistics.fmean(invested) * 100, 1) if invested else 0.0,
                  index_sharpe=round(statistics.fmean(idx_daily) / idx_sd * 252 ** 0.5, 2) if idx_sd else 0.0,
                  index_drawdown_pct=round(iworst * 100, 1))


def with_control(recipe: Recipe, *args, **kwargs) -> Result:
    """The recipe's run, with the random control's mean annual return attached."""
    mine = simulate(recipe, *args, **kwargs)
    controls = [simulate(recipe, *args, **kwargs, rng=_random.Random(seed)).annual_pct
                for seed in range(RANDOM_SEEDS)]
    return mine.model_copy(update={"random_annual_pct": round(statistics.fmean(controls), 2)})
