"""Liquidity-sweep signal with its own exits, for the live paper books (operator 2026-10-22).

From the article the operator shared (ITI, "Liquidity Sweeps"): price trades
beyond a marked low, the candle CLOSES back inside, ideally on a volume surge;
enter after the close, stop below the wick, take half at fair value (VWAP), the
rest at the other side of the range. Long only. Same-day trade.

RULES (fixed 2026-10-22 before the first live candle):
  level     the prior session's low, or the lowest low of the LOOKBACK candles
            before the sweep candle (whichever the candle swept; the deeper one)
  sweep     candle low < level, close > level, volume >= VOL_SURGE x the average
            of the LOOKBACK candles before it
  entry     the live price after the sweep candle closes
  stop      the sweep candle's low - 0.1 x ATR(14)
  target 1  session VWAP, if above the entry, else entry + 1R: HALF the position;
            the stop on the other half moves to the entry
  target 2  the highest high of the LOOKBACK candles before the sweep, if above
            target 1, else entry + 2R: the other half
  time      everything out by LAST_EXIT ET; no entry after LAST_ENTRY
The "+scalp" variant (added once TRIALS #83 is read) takes everything at target 1.
VIRTUAL money only (signals/paper.py's NOTIONAL). PURE.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, time

from scanner.models import Frozen
from scanner.signals import indicators as ind
from scanner.signals import paper, rules

LOOKBACK = 20
VOL_SURGE = 2.0
LAST_ENTRY, LAST_EXIT = time(15, 30), time(15, 55)


class Setup(Frozen):
    level: float
    stop: float
    target1: float
    target2: float
    signal_bar: datetime


def vwap(times: Sequence[datetime], h, lo, c, v) -> float | None:
    """Session VWAP from the newest candle's session start."""
    day = times[-1].date()
    pv = vol = 0.0
    for t, a, b, d, x in zip(times, h, lo, c, v):
        if t.date() == day:
            pv += (a + b + d) / 3 * x
            vol += x
    return pv / vol if vol else None


def detect(times: Sequence[datetime], o, h, lo, c, v, *, price: float) -> Setup | None:
    """A sweep on the newest COMPLETED candle -> the trade plan, else None."""
    i = len(c) - 1
    if i < LOOKBACK + 1 or times[i].time() >= LAST_ENTRY:
        return None
    prior = range(i - LOOKBACK, i)
    swing_low = min(lo[k] for k in prior)
    day = times[i].date()
    prev_day = [k for k in range(i) if times[k].date() < day]
    prev_low = None
    if prev_day:
        last = times[prev_day[-1]].date()
        prev_low = min(lo[k] for k in prev_day if times[k].date() == last)
    swept = [x for x in (swing_low, prev_low) if x is not None and lo[i] < x < c[i]]
    avg_vol = sum(v[k] for k in prior) / LOOKBACK
    if not swept or not avg_vol or v[i] < VOL_SURGE * avg_vol:
        return None
    a = ind.atr(h, lo, c, 14)[i] or 0.0
    stop = lo[i] - 0.1 * a
    risk = price - stop
    if risk <= 0:
        return None
    fair = vwap(times, h, lo, c, v)
    t1 = fair if fair is not None and fair > price else price + risk
    hi = max(h[k] for k in prior)
    t2 = hi if hi > t1 else price + 2 * risk
    return Setup(level=min(swept), stop=stop, target1=t1, target2=t2, signal_bar=times[i])


class Open(Frozen):
    symbol: str
    entry: float
    entry_at: datetime
    stop: float
    target1: float
    target2: float
    signal_bar: datetime
    half_done: bool = False


def _closed(version: str, p: Open, price: float, at: datetime, reason: str, share: float) -> paper.Closed:
    net = (price * (1 - rules.COST)) / (p.entry * (1 + rules.COST)) - 1
    return paper.Closed(version=version, symbol=p.symbol, entry=p.entry, entry_at=p.entry_at, exit=price,
                        exit_at=at, reason=reason, net_pct=net * 100 * share,
                        dollars=net * paper.NOTIONAL * share)


def manage(version: str, p: Open, *, times, highs, lows, price: float, now: datetime,
           scalp: bool) -> tuple[Open | None, list[paper.Closed]]:
    """Step an open sweep trade on candles completed after its signal candle."""
    out: list[paper.Closed] = []
    share = 1.0 if scalp else (0.5 if p.half_done else 1.0)
    for t, hi, lo in zip(times, highs, lows):
        if t <= p.signal_bar:
            continue
        if lo <= p.stop:
            out.append(_closed(version, p, p.stop, now, "stop" if not p.half_done else "breakeven", share))
            return None, out
        if not p.half_done and hi >= p.target1:
            if scalp:
                out.append(_closed(version, p, p.target1, now, "target 1 (all)", 1.0))
                return None, out
            out.append(_closed(version, p, p.target1, now, "target 1 (half)", 0.5))
            p = p.model_copy(update={"half_done": True, "stop": p.entry})
            share = 0.5
        if p.half_done and hi >= p.target2:
            out.append(_closed(version, p, p.target2, now, "target 2", 0.5))
            return None, out
    if now.time() >= LAST_EXIT or now.date() > p.entry_at.date():
        out.append(_closed(version, p, price, now, "session end", share))
        return None, out
    return p, out
