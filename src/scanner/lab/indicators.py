"""Every indicator on the lab's menu, for one stock's whole history in one pass.

Value i uses bars 0..i only -- the decision is made on day i's close and the
buy happens on day i+1's close (sim.py), so nothing here can see the future.
None where the history is too short. PURE: lists in, lists out, no numpy
(not installed here; one O(n) pass per series is fast enough).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from scanner.features.bars import Bars

Series = list[float | None]


def sma(values: Sequence[float], n: int) -> Series:
    out: Series = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def stdev(values: Sequence[float], n: int) -> Series:
    out: Series = [None] * len(values)
    s = s2 = 0.0
    for i, v in enumerate(values):
        s += v
        s2 += v * v
        if i >= n:
            old = values[i - n]
            s -= old
            s2 -= old * old
        if i >= n - 1:
            mean = s / n
            out[i] = max(0.0, s2 / n - mean * mean) ** 0.5
    return out


def wilder(values: Sequence[float | None], n: int) -> Series:
    """Wilder's smoothing (RSI, ATR, ADX), seeded with the first n-value mean."""
    out: Series = [None] * len(values)
    seed: list[float] = []
    prev = None
    for i, v in enumerate(values):
        if v is None:
            continue
        if prev is None:
            seed.append(v)
            if len(seed) == n:
                prev = sum(seed) / n
                out[i] = prev
            continue
        prev = (prev * (n - 1) + v) / n
        out[i] = prev
    return out


def rsi(closes: Sequence[float], n: int = 14) -> Series:
    ups: list[float | None] = [None]
    downs: list[float | None] = [None]
    for a, b in zip(closes, closes[1:]):
        ups.append(max(b - a, 0.0))
        downs.append(max(a - b, 0.0))
    up, down = wilder(ups, n), wilder(downs, n)
    out: Series = []
    for u, d in zip(up, down):
        out.append(None if u is None or d is None else (100.0 if d == 0 else 100 - 100 / (1 + u / d)))
    return out


def true_range(highs, lows, closes) -> list[float | None]:
    out: list[float | None] = [None]
    for i in range(1, len(closes)):
        pc = closes[i - 1]
        out.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    return out


def adx(highs, lows, closes, n: int = 14) -> Series:
    plus: list[float | None] = [None]
    minus: list[float | None] = [None]
    for i in range(1, len(closes)):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
    tr, p, m = wilder(true_range(highs, lows, closes), n), wilder(plus, n), wilder(minus, n)
    dx: list[float | None] = []
    for t, a, b in zip(tr, p, m):
        if t is None or a is None or b is None or t == 0 or (a + b) == 0:
            dx.append(None)
            continue
        di_p, di_m = 100 * a / t, 100 * b / t
        dx.append(100 * abs(di_p - di_m) / (di_p + di_m))
    return wilder(dx, n)


def prior_high(highs: Sequence[float], n: int) -> Series:
    """Highest high of the n days BEFORE day i (a close above it is a new n-day high)."""
    rolled = _rolling_max(highs, n)
    return [None] + rolled[:-1] if highs else []


def _pct(a: float | None, b: float | None) -> float | None:
    return None if a is None or not b else (a / b - 1) * 100


def compute(bars: Bars) -> dict[str, Series]:
    """{indicator name: one value per bar}. Names match recipe.NUMERIC and recipe.FLAGS."""
    c, h, lo, o, v = bars.closes, bars.highs, bars.lows, bars.opens, bars.volumes
    n = len(c)
    out: dict[str, Series] = {}
    for k in (20, 50, 100, 200):
        avg = sma(c, k)
        out[f"price_vs_sma{k}_pct"] = [_pct(c[i], avg[i]) for i in range(n)]
    out["rsi14"] = rsi(c)
    out["ret_1m_pct"] = [_pct(c[i], c[i - 21]) if i >= 21 else None for i in range(n)]
    out["ret_3m_pct"] = [_pct(c[i], c[i - 63]) if i >= 63 else None for i in range(n)]
    out["mom_12_1_pct"] = [_pct(c[i - 21], c[i - 252]) if i >= 252 else None for i in range(n)]
    hi252 = _rolling_max(h, 252)
    out["pct_below_52w_high"] = [None if hi252[i] is None else (1 - c[i] / hi252[i]) * 100 for i in range(n)]
    v20, v10 = sma(v, 20), sma(v, 10)
    # Today against the PRIOR days' average, so today's own volume does not dilute it.
    out["volume_vs_20d"] = [v[i] / v20[i - 1] if i >= 1 and v20[i - 1] else None for i in range(n)]
    out["volume_vs_10d"] = [v[i] / v10[i - 1] if i >= 1 and v10[i - 1] else None for i in range(n)]
    atr = wilder(true_range(h, lo, c), 14)
    out["atr_pct"] = [None if atr[i] is None or not c[i] else atr[i] / c[i] * 100 for i in range(n)]
    out["atr"] = atr
    out["adx14"] = adx(h, lo, c)
    mid, sd = sma(c, 20), stdev(c, 20)
    out["bb_width_pct"] = [None if mid[i] is None or not mid[i] else 4 * sd[i] / mid[i] * 100 for i in range(n)]
    out["gap_pct"] = [_pct(o[i], c[i - 1]) if i >= 1 else None for i in range(n)]
    out["close_in_range"] = [((c[i] - lo[i]) / (h[i] - lo[i])) if h[i] > lo[i] else 0.5 for i in range(n)]
    p20, p55 = prior_high(h, 20), prior_high(h, 55)
    sma20, sma200 = sma(c, 20), sma(c, 200)
    out["new_high_20"] = [None if p20[i] is None else float(c[i] > p20[i]) for i in range(n)]
    out["new_high_55"] = [None if p55[i] is None else float(c[i] > p55[i]) for i in range(n)]
    out["pullback"] = [None if sma200[i] is None else float(sma200[i] < c[i] < sma20[i]) for i in range(n)]
    return out


def _rolling_max(values: Sequence[float], n: int) -> Series:
    """Max of the last n values (including i), O(len) with a monotonic deque."""
    from collections import deque
    out: Series = [None] * len(values)
    window: deque[int] = deque()
    for i, v in enumerate(values):
        while window and values[window[-1]] <= v:
            window.pop()
        window.append(i)
        if window[0] <= i - n:
            window.popleft()
        if i >= n - 1:
            out[i] = values[window[0]]
    return out


def ema(values: Sequence[float | None], n: int) -> Series:
    """Exponential average seeded with the first n-value mean; None until then."""
    out: Series = [None] * len(values)
    k, seed, prev = 2 / (n + 1), [], None
    for i, v in enumerate(values):
        if v is None:
            continue
        if prev is None:
            seed.append(v)
            if len(seed) == n:
                prev = sum(seed) / n
                out[i] = prev
            continue
        prev = v * k + prev * (1 - k)
        out[i] = prev
    return out


def _rolling_min(values: Sequence[float], n: int) -> Series:
    neg = _rolling_max([-v for v in values], n)
    return [None if v is None else -v for v in neg]


def _rolling_sum(values: Sequence[float], n: int) -> Series:
    out: Series = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total
    return out


def supertrend_up(highs, lows, closes, n: int = 10, mult: float = 3.0) -> Series:
    """1.0 while the SuperTrend (ATR n, multiplier mult) is up, 0.0 while down."""
    atr = wilder(true_range(highs, lows, closes), n)
    out: Series = [None] * len(closes)
    upper = lower = None
    up = True
    for i, a in enumerate(atr):
        if a is None:
            continue
        mid = (highs[i] + lows[i]) / 2
        bu, bl = mid + mult * a, mid - mult * a
        if upper is None:
            upper, lower = bu, bl
        else:
            upper = bu if bu < upper or closes[i - 1] > upper else upper
            lower = bl if bl > lower or closes[i - 1] < lower else lower
        if up and closes[i] < lower:
            up = False
        elif not up and closes[i] > upper:
            up = True
        out[i] = float(up)
    return out


def psar_up(highs, lows, step: float = 0.02, max_step: float = 0.2) -> Series:
    """1.0 while Wilder's Parabolic SAR (classic 0.02 / 0.2) is under price (uptrend), else 0.0."""
    n = len(highs)
    out: Series = [None] * n
    if n < 3:
        return out
    up, af = highs[1] >= highs[0], step
    sar = lows[0] if up else highs[0]
    ep = highs[1] if up else lows[1]
    for i in range(2, n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, lows[i - 1], lows[i - 2])
            if lows[i] < sar:
                up, sar, ep, af = False, ep, lows[i], step
            elif highs[i] > ep:
                ep, af = highs[i], min(af + step, max_step)
        else:
            sar = max(sar, highs[i - 1], highs[i - 2])
            if highs[i] > sar:
                up, sar, ep, af = True, ep, highs[i], step
            elif lows[i] < ep:
                ep, af = lows[i], min(af + step, max_step)
        out[i] = float(up)
    return out


def _fresh(flag: Series, days: int = 5) -> Series:
    """1.0 when `flag` turned on (0 -> 1) within the last `days` bars and is still on."""
    out: Series = [None] * len(flag)
    last_flip = None
    for i, v in enumerate(flag):
        if v is None:
            continue
        if v >= 0.5 and i and flag[i - 1] is not None and flag[i - 1] < 0.5:
            last_flip = i
        out[i] = float(v >= 0.5 and last_flip is not None and i - last_flip < days)
    return out


def more(bars: Bars, market: Mapping[date, float] | None = None) -> dict[str, Series]:
    """The second menu (operator 2026-10-23: "give her all the indicators ... market hours,
    after hours, days of the week, months"). Daily bars: the OVERNIGHT move (after-hours +
    premarket) is yesterday's close -> today's open; the REGULAR SESSION is open -> close."""
    c, h, lo, o, v, d = bars.closes, bars.highs, bars.lows, bars.opens, bars.volumes, bars.dates
    n = len(c)
    out: dict[str, Series] = {}
    fast, slow = ema(c, 12), ema(c, 26)
    line = [None if a is None or b is None else a - b for a, b in zip(fast, slow)]
    signal = ema(line, 9)
    out["macd_hist_pct"] = [None if line[i] is None or signal[i] is None or not c[i]
                            else (line[i] - signal[i]) / c[i] * 100 for i in range(n)]
    out["macd_above_signal"] = [None if line[i] is None or signal[i] is None
                                else float(line[i] > signal[i]) for i in range(n)]
    lo14, hi14 = _rolling_min(lo, 14), _rolling_max(h, 14)
    out["stoch_k14"] = [None if lo14[i] is None or hi14[i] == lo14[i]
                        else (c[i] - lo14[i]) / (hi14[i] - lo14[i]) * 100 for i in range(n)]
    tp = [(h[i] + lo[i] + c[i]) / 3 for i in range(n)]
    pos = [0.0] + [tp[i] * v[i] if tp[i] > tp[i - 1] else 0.0 for i in range(1, n)]
    neg = [0.0] + [tp[i] * v[i] if tp[i] < tp[i - 1] else 0.0 for i in range(1, n)]
    ps, ns = _rolling_sum(pos, 14), _rolling_sum(neg, 14)
    # Rolling sums drift by float rounding: a "zero" can come back as -1e-12.
    out["mfi14"] = [None if ps[i] is None else
                    (100.0 if ns[i] <= 1e-9 else 100 * max(ps[i], 0.0) / (max(ps[i], 0.0) + ns[i]))
                    for i in range(n)]
    obv, run = [], 0.0
    for i in range(n):
        if i and c[i] > c[i - 1]:
            run += v[i]
        elif i and c[i] < c[i - 1]:
            run -= v[i]
        obv.append(run)
    obv20 = sma(obv, 20)
    out["obv_rising"] = [None if obv20[i] is None else float(obv[i] > obv20[i]) for i in range(n)]
    out["ret_1w_pct"] = [_pct(c[i], c[i - 5]) if i >= 5 else None for i in range(n)]
    out["ret_6m_pct"] = [_pct(c[i], c[i - 126]) if i >= 126 else None for i in range(n)]
    lo252 = _rolling_min(lo, 252)
    out["pct_above_52w_low"] = [_pct(c[i], lo252[i]) for i in range(n)]
    mid, sd = sma(c, 20), stdev(c, 20)
    out["bb_pct_b"] = [None if mid[i] is None or not sd[i] else (c[i] - (mid[i] - 2 * sd[i])) / (4 * sd[i])
                       for i in range(n)]
    atr = wilder(true_range(h, lo, c), 14)
    atr50 = sma([a if a is not None else 0.0 for a in atr], 50)
    out["atr_vs_50d"] = [None if atr[i] is None or i < 63 or not atr50[i] else atr[i] / atr50[i]
                         for i in range(n)]
    streak, s = [], 0
    for i in range(n):
        if i and c[i] > c[i - 1]:
            s = s + 1 if s > 0 else 1
        elif i and c[i] < c[i - 1]:
            s = s - 1 if s < 0 else -1
        else:
            s = 0
        streak.append(float(s))
    out["streak"] = streak
    out["supertrend_up"] = supertrend_up(h, lo, c)
    intraday = [_pct(c[i], o[i]) or 0.0 for i in range(n)]
    gaps = [0.0] + [_pct(o[i], c[i - 1]) or 0.0 for i in range(1, n)]
    out["intraday_pct"] = intraday
    out["intraday_5d_pct"] = _rolling_sum(intraday, 5)
    out["overnight_5d_pct"] = _rolling_sum(gaps, 5)
    v20, v60 = sma(v, 20), sma(v, 60)
    out["volume_trend"] = [None if v20[i] is None or v60[i] is None or not v60[i] else v20[i] / v60[i]
                           for i in range(n)]
    upv = [0.0] + [v[i] if c[i] > c[i - 1] else 0.0 for i in range(1, n)]
    dnv = [0.0] + [v[i] if c[i] < c[i - 1] else 0.0 for i in range(1, n)]
    us, ds = _rolling_sum(upv, 20), _rolling_sum(dnv, 20)
    out["up_volume_ratio"] = [None if us[i] is None or ds[i] is None or ds[i] <= 1e-9 else max(us[i], 0.0) / ds[i]
                              for i in range(n)]
    pv = _rolling_sum([c[i] * v[i] for i in range(n)], 20)
    vs = _rolling_sum(list(v), 20)
    out["close_vs_vwap20_pct"] = [None if pv[i] is None or not vs[i] or vs[i] <= 1e-9 else _pct(c[i], pv[i] / vs[i])
                                  for i in range(n)]
    out["dollar_volume_m"] = [None if pv[i] is None else pv[i] / 20 / 1e6 for i in range(n)]
    # Classic crossovers and Parabolic SAR at their standard settings (operator 2026-10-23,
    # from everget's toolkit -- fixed, NOT optimised per stock: picking the best of hundreds of
    # settings on the same history is the luck the hidden years exist to catch).
    e10, e50 = ema(c, 10), ema(c, 50)
    s50, s200 = sma(c, 50), sma(c, 200)
    ema_up = [None if e10[i] is None or e50[i] is None else float(e10[i] > e50[i]) for i in range(n)]
    golden = [None if s50[i] is None or s200[i] is None else float(s50[i] > s200[i]) for i in range(n)]
    sar = psar_up(h, lo)
    out["ema10_above_ema50"] = ema_up
    out["ema_cross_up_5d"] = _fresh(ema_up)
    out["golden_cross"] = golden
    out["golden_cross_5d"] = _fresh(golden)
    out["psar_up"] = sar
    out["psar_flip_up_5d"] = _fresh(sar)
    out["day_of_week"] = [float(x.weekday() + 1) for x in d]
    out["month"] = [float(x.month) for x in d]
    out["day_of_month"] = [float(x.day) for x in d]
    if market:
        mk = [market.get(x) for x in d]
        out["rs_3m_pct"] = [None if i < 63 or mk[i] is None or mk[i - 63] is None
                            else (_pct(c[i], c[i - 63]) or 0.0) - (_pct(mk[i], mk[i - 63]) or 0.0)
                            for i in range(n)]
    return out


def market_uptrend(spx: Bars) -> dict:
    """{date: 1.0 if the S&P 500 closed above its 200-day average, 0.0 if not}."""
    avg = sma(spx.closes, 200)
    return {d: float(c > a) for d, c, a in zip(spx.dates, spx.closes, avg) if a is not None}
