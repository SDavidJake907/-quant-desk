"""Every indicator on the lab's menu, for one stock's whole history in one pass.

Value i uses bars 0..i only -- the decision is made on day i's close and the
buy happens on day i+1's close (sim.py), so nothing here can see the future.
None where the history is too short. PURE: lists in, lists out, no numpy
(not installed here; one O(n) pass per series is fast enough).
"""

from __future__ import annotations

from collections.abc import Sequence

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


def market_uptrend(spx: Bars) -> dict:
    """{date: 1.0 if the S&P 500 closed above its 200-day average, 0.0 if not}."""
    avg = sma(spx.closes, 200)
    return {d: float(c > a) for d, c, a in zip(spx.dates, spx.closes, avg) if a is not None}
