"""Pine Script indicators, translated to plain Python (operator 2026-10-21).

Source: the operator's downloads -- everget's tradingview-pinescript-indicators
(GPL-3.0) and TradingView built-ins. Each function follows its Pine script
line for line, including Pine's seeding: ta.ema / ta.rma start from the SMA of
the first `length` values, and a value is None (Pine's na) until it exists.

Inputs are equal-length lists, oldest first. Every output is a list of the same
length; bar i uses bars 0..i only (no lookahead). PURE.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Series = list[float | None]


def sma(x: Sequence[float | None], n: int) -> Series:
    out: Series = [None] * len(x)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        if all(v is not None for v in w):
            out[i] = sum(w) / n  # type: ignore[arg-type]
    return out


def _smooth(x: Sequence[float | None], n: int, alpha: float) -> Series:
    """Pine's ema/rma: seeded with the SMA of the first n values."""
    out: Series = [None] * len(x)
    seed = sma(x, n)
    for i, v in enumerate(x):
        prev = out[i - 1] if i else None
        if prev is None:
            out[i] = seed[i]
        elif v is not None:
            out[i] = alpha * v + (1 - alpha) * prev
        else:
            out[i] = prev
    return out


def ema(x: Sequence[float | None], n: int) -> Series:
    return _smooth(x, n, 2 / (n + 1))


def rma(x: Sequence[float | None], n: int) -> Series:
    return _smooth(x, n, 1 / n)


def true_range(h: Sequence[float], lo: Sequence[float], c: Sequence[float]) -> list[float]:
    return [h[0] - lo[0]] + [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
                             for i in range(1, len(c))]


def atr(h, lo, c, n: int = 14) -> Series:
    return rma(true_range(h, lo, c), n)


def highest(x: Sequence[float], n: int) -> Series:
    return [max(x[i - n + 1:i + 1]) if i >= n - 1 else None for i in range(len(x))]


def lowest(x: Sequence[float], n: int) -> Series:
    return [min(x[i - n + 1:i + 1]) if i >= n - 1 else None for i in range(len(x))]


def rsi(x: Sequence[float], n: int = 14) -> Series:
    ch = [None] + [x[i] - x[i - 1] for i in range(1, len(x))]
    up = rma([None if d is None else max(d, 0.0) for d in ch], n)
    dn = rma([None if d is None else max(-d, 0.0) for d in ch], n)
    out: Series = []
    for u, d in zip(up, dn):
        if u is None or d is None:
            out.append(None)
        else:
            out.append(100.0 if d == 0 else 0.0 if u == 0 else 100 - 100 / (1 + u / d))
    return out


# --- trailing stops (everget, trailing_stops/) -------------------------------

def supertrend(o, h, lo, c, n: int = 22, mult: float = 3.0, wicks: bool = True) -> list[int]:
    """Direction per bar: 1 up, -1 down (supertrend.pine, src = hl2)."""
    a = atr(h, lo, c, n)
    hp, lp = (h, lo) if wicks else (c, c)
    dirs, d = [], 1
    long_prev = short_prev = None
    for i in range(len(c)):
        if a[i] is None:
            dirs.append(d)
            continue
        src = (h[i] + lo[i]) / 2
        doji = o[i] == c[i] == lo[i] == h[i]
        ls, ss = src - mult * a[i], src + mult * a[i]
        lsp = ls if long_prev is None else long_prev
        ssp = ss if short_prev is None else short_prev
        if ls > 0:
            ls = lsp if doji else (max(ls, lsp) if i and lp[i - 1] > lsp else ls)
        else:
            ls = lsp
        if ss > 0:
            ss = ssp if doji else (min(ss, ssp) if i and hp[i - 1] < ssp else ss)
        else:
            ss = ssp
        d = 1 if d == -1 and hp[i] > ssp else -1 if d == 1 and lp[i] < lsp else d
        long_prev, short_prev = ls, ss
        dirs.append(d)
    return dirs


def chandelier(h, lo, c, n: int = 22, mult: float = 3.0, use_close: bool = True) -> list[int]:
    """Direction per bar (chandelier_exit.pine)."""
    a = atr(h, lo, c, n)
    hi = highest(c if use_close else h, n)
    lw = lowest(c if use_close else lo, n)
    dirs, d = [], 1
    long_prev = short_prev = None
    for i in range(len(c)):
        if a[i] is None or hi[i] is None:
            dirs.append(d)
            continue
        ls, ss = hi[i] - mult * a[i], lw[i] + mult * a[i]
        lsp = ls if long_prev is None else long_prev
        ssp = ss if short_prev is None else short_prev
        ls = max(ls, lsp) if c[i - 1] > lsp else ls
        ss = min(ss, ssp) if c[i - 1] < ssp else ss
        d = 1 if c[i] > ssp else -1 if c[i] < lsp else d
        long_prev, short_prev = ls, ss
        dirs.append(d)
    return dirs


def halftrend(h, lo, c, amplitude: int = 2, channel_dev: int = 2) -> list[int]:
    """Direction per bar: 1 up (Pine trend == 0), -1 down (halftrend.pine). Its
    ATR(100) only draws the channel, so it is left out."""
    hi_ma, lo_ma = sma(h, amplitude), sma(lo, amplitude)
    hi_p, lo_p = highest(h, amplitude), lowest(lo, amplitude)
    trend = next_trend = 0
    max_low = lo[0]
    min_high = h[0]
    up = down = 0.0
    dirs = []
    for i in range(len(c)):
        if hi_ma[i] is None:
            dirs.append(1)
            continue
        prev_trend = trend
        if next_trend == 1:
            max_low = max(lo_p[i], max_low)
            if hi_ma[i] < max_low and c[i] < lo[i - 1]:
                trend, next_trend, min_high = 1, 0, hi_p[i]
        else:
            min_high = min(hi_p[i], min_high)
            if lo_ma[i] > min_high and c[i] > h[i - 1]:
                trend, next_trend, max_low = 0, 1, lo_p[i]
        if trend == 0:
            up = down if prev_trend != 0 else max(max_low, up)
        else:
            down = up if prev_trend != 1 else min(min_high, down)
        dirs.append(1 if trend == 0 else -1)
    return dirs


def psar(h, lo, start: float = 0.02, inc: float = 0.02, maximum: float = 0.2,
         c: Sequence[float] | None = None) -> Series:
    """Parabolic SAR exactly as Pine's ta.sar (TradingView's published pine_sar).
    The first trend is set by close vs the prior close (falls back to highs when
    closes are not given). 2026-10-22: the earlier textbook Wilder version agreed
    with ta.sar on only 76% of MRVL 15m candles (tools/pinets/check_translations.py)."""
    n = len(h)
    out: Series = [None] * n
    if n < 2:
        return out
    closes = c if c is not None else h
    result = max_min = accel = 0.0
    below = False
    for i in range(1, n):
        first = False
        if i == 1:
            if closes[1] > closes[0]:
                below, max_min, result = True, h[1], lo[0]
            else:
                below, max_min, result = False, lo[1], h[0]
            first, accel = True, start
        result = result + accel * (max_min - result)
        if below:
            if result > lo[i]:
                first, below = True, False
                result, max_min, accel = max(h[i], max_min), lo[i], start
        else:
            if result < h[i]:
                first, below = True, True
                result, max_min, accel = min(lo[i], max_min), h[i], start
        if not first:
            if below:
                if h[i] > max_min:
                    max_min, accel = h[i], min(accel + inc, maximum)
            elif lo[i] < max_min:
                max_min, accel = lo[i], min(accel + inc, maximum)
        if below:
            result = min(result, lo[i - 1], lo[i - 2]) if i > 1 else min(result, lo[i - 1])
        else:
            result = max(result, h[i - 1], h[i - 2]) if i > 1 else max(result, h[i - 1])
        out[i] = result
    return out


def nrtr(c, pct: float = 2.0) -> list[int]:
    """Direction per bar (nrtr_nick_rypock_trailing_reverse.pine)."""
    k = pct / 100
    trend, hp, lp = 0, c[0], c[0]
    dirs = []
    for x in c:
        if trend >= 0:
            hp = max(hp, x)
            if x <= hp * (1 - k):
                trend, lp = -1, x
        else:
            lp = min(lp, x)
            if x > lp * (1 + k):
                trend, hp = 1, x
        dirs.append(1 if trend >= 0 else -1)
    return dirs


# --- oscillators and volume ---------------------------------------------------

def _stoch(x: Series, n: int) -> Series:
    """Pine stoch(x, x, x, n) with fixnan then nz (stc_schaff_trend_cycle.pine)."""
    out: Series = []
    last = None
    for i in range(len(x)):
        w = x[max(0, i - n + 1):i + 1]
        val = None
        if i >= n - 1 and all(v is not None for v in w):
            hi, lw = max(w), min(w)  # type: ignore[type-var]
            val = 100 * (x[i] - lw) / (hi - lw) if hi != lw else None  # type: ignore[operator]
        last = val if val is not None else last
        out.append(last if last is not None else 0.0)
    return out


def stc(c, fast: int = 23, slow: int = 50, cycle: int = 10, d1: int = 3, d2: int = 3) -> Series:
    ef, es = ema(c, fast), ema(c, slow)
    macd = [None if a is None or b is None else a - b for a, b in zip(ef, es)]
    k = _stoch(macd, cycle)
    kd = _stoch(ema(k, d1), cycle)
    return [None if v is None else max(min(v, 100.0), 0.0) for v in ema(kd, d2)]


def connors_rsi(c, rsi_len: int = 3, streak_len: int = 2, rank_len: int = 100) -> Series:
    streak, s = [], 0
    for i in range(len(c)):
        if i and c[i] > c[i - 1]:
            s = s + 1 if s >= 0 else 1
        elif i and c[i] < c[i - 1]:
            s = s - 1 if s <= 0 else -1
        else:
            s = 0
        streak.append(float(s))
    r1, r2 = rsi(c, rsi_len), rsi(streak, streak_len)
    roc = [None] + [(c[i] / c[i - 1] - 1) * 100 for i in range(1, len(c))]
    out: Series = []
    for i in range(len(c)):
        if i <= rank_len or r1[i] is None or r2[i] is None:
            out.append(None)
            continue
        prev = roc[i - rank_len:i]
        rank = 100 * sum(1 for v in prev if v is not None and v <= roc[i]) / rank_len
        out.append((r1[i] + r2[i] + rank) / 3)
    return out


def mfi(h, lo, c, v, n: int = 14) -> Series:
    src = [(a + b + d) / 3 for a, b, d in zip(h, lo, c)]
    pos = [0.0] + [v[i] * src[i] if src[i] > src[i - 1] else 0.0 for i in range(1, len(c))]
    neg = [0.0] + [v[i] * src[i] if src[i] < src[i - 1] else 0.0 for i in range(1, len(c))]
    out: Series = []
    for i in range(len(c)):
        if i < n:
            out.append(None)
            continue
        up, dn = sum(pos[i - n + 1:i + 1]), sum(neg[i - n + 1:i + 1])
        out.append(100.0 if dn == 0 else 100 - 100 / (1 + up / dn))
    return out


def pvt(c, v) -> list[float]:
    out, acc = [0.0], 0.0
    for i in range(1, len(c)):
        acc += (c[i] - c[i - 1]) / c[i - 1] * v[i] if c[i - 1] else 0.0
        out.append(acc)
    return out


def obv(c, v) -> list[float]:
    out, acc = [0.0], 0.0
    for i in range(1, len(c)):
        acc += math.copysign(v[i], c[i] - c[i - 1]) if c[i] != c[i - 1] else 0.0
        out.append(acc)
    return out


def adx(h, lo, c, di_len: int = 14, adx_len: int = 14) -> Series:
    """ta.dmi's ADX (the operator's MyADX14-40.pine)."""
    up = [None] + [h[i] - h[i - 1] for i in range(1, len(c))]
    dn = [None] + [lo[i - 1] - lo[i] for i in range(1, len(c))]
    pdm = [None if u is None else (u if u > d and u > 0 else 0.0) for u, d in zip(up, dn)]
    mdm = [None if d is None else (d if d > u and d > 0 else 0.0) for u, d in zip(up, dn)]
    tr = rma(true_range(h, lo, c), di_len)
    ps, ms = rma(pdm, di_len), rma(mdm, di_len)
    dx: Series = []
    for t, p, m in zip(tr, ps, ms):
        if t is None or p is None or m is None or t == 0:
            dx.append(None)
            continue
        plus, minus = 100 * p / t, 100 * m / t
        dx.append(abs(plus - minus) / (plus + minus if plus + minus else 1))
    return [None if x is None else 100 * x for x in rma(dx, adx_len)]
