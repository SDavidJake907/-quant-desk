"""Does any indicator make momentum better? A cross-sectional screen, fixed first.

    python scripts/indicator_screen_test.py

READ-ONLY research. Changes nothing live.

WHY THIS SHAPE
"Add every indicator and see" is how backtests fool people: with ~61 trials
already (TRIALS.md), one of twelve new indicators will look significant by
luck. So the list, the directions, the bars and the method are fixed here,
committed before the run, and the result counts all twelve as trials.
Indicator layers on the sweep strategy were already null (WORKLIST §27).

DATA
S&P 500 + MidCap 400 point-in-time union (operator's universe), Yahoo daily,
2016-2026; MidCap membership approximate before 2018 (§31). Each month-end,
every member with 273+ bars is scored; outcome = the next 63 trading days'
return from the NEXT close, minus that month's universe average (so the market
cancels). Survivorship lifts all names alike; a spread between groups is less
affected than a level.

INDICATORS -- FIXED 2026-10-05, BEFORE THE RUN
  published momentum add-ons (judged at t >= 2 in the STATED direction):
    hi52     close / 52-week high                       + (George & Hwang 2004)
    mom12_7  return t-252 -> t-126                       + (Novy-Marx 2012)
    rev1m    return over the last 21 days                - (short-term reversal)
    fip      share of up days, t-252 -> t-21             + (Da, Gurun & Warachka 2014)
    atr      14-day ATR / price                          - (low-volatility effect)
  classic technical indicators (judged at |t| >= 3.3, either direction --
  no strong published out-of-sample evidence; Bonferroni over ~70 trials):
    rsi      RSI(14)
    macd     MACD(12,26,9) histogram / price
    sma50    close / 50-day average - 1
    sma200   close / 200-day average - 1
    bb       Bollinger %B (20, 2)
    volr     20-day / 200-day average volume
  control: mom12_1 itself (should be positive in A)

TWO QUESTIONS PER INDICATOR
  A  alone: monthly quintiles across the universe, Q5 - Q1 spread
  B  INSIDE MOMENTUM -- the operator's question: among the top momentum
     quintile that month, names above the indicator's median minus names
     below it
  Spreads are per month, 63-day overlapping, so Newey-West t with lag 2.
PASS for "makes momentum better": B meets its bar in its direction AND has
the same sign in 2016-2020 and 2021-2026. A is reported, not scored.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from collections import defaultdict
from datetime import date

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.features.bars import fetch_bars, fetch_many
from scanner.sources import index_history
from scanner.sources.index_history import SP400, SP500

sys.path.insert(0, "scripts")
from momentum_guard_test import union_membership, window  # noqa: E402
from momentum_hac_test import newey_west_t  # noqa: E402

LO, HI, SPLIT = date(2015, 6, 1), date(2026, 10, 2), date(2021, 1, 1)
HOLD, MIN_BARS = 63, 273
PUBLISHED = {"hi52": 1, "mom12_7": 1, "rev1m": -1, "fip": 1, "atr": -1}
CLASSIC = ("rsi", "macd", "sma50", "sma200", "bb", "volr")
CONTROL = "mom12_1"
T_PUBLISHED, T_CLASSIC = 2.0, 3.3


def ema(values, n):
    k, out, e = 2 / (n + 1), [], values[0]
    for v in values:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def series(bars) -> dict[str, list[float | None]]:
    """Every indicator for every day of one symbol, computed once (O(n))."""
    c, h, lo, v = list(bars.closes), list(bars.highs), list(bars.lows), list(bars.volumes)
    n = len(c)
    out: dict[str, list[float | None]] = {k: [None] * n for k in
                                          (*PUBLISHED, *CLASSIC, CONTROL)}
    gains, losses, tr = [0.0] * n, [0.0] * n, [0.0] * n
    for i in range(1, n):
        d = c[i] - c[i - 1]
        gains[i], losses[i] = max(d, 0.0), max(-d, 0.0)
        tr[i] = max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
    ag = al = atr = None
    e12, e26 = ema(c, 12), ema(c, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ema(macd, 9)
    for i in range(n):
        if i >= 14:
            if ag is None:
                ag, al = statistics.fmean(gains[1:15]), statistics.fmean(losses[1:15])
                atr = statistics.fmean(tr[1:15])
            else:
                ag, al = (ag * 13 + gains[i]) / 14, (al * 13 + losses[i]) / 14
                atr = (atr * 13 + tr[i]) / 14
            out["rsi"][i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
            out["atr"][i] = atr / c[i]
        if i >= 34:
            out["macd"][i] = (macd[i] - sig[i]) / c[i]
        if i >= 19:
            w = c[i - 19:i + 1]
            m, sd = statistics.fmean(w), statistics.pstdev(w)
            out["bb"][i] = (c[i] - (m - 2 * sd)) / (4 * sd) if sd else None
        if i >= 49:
            out["sma50"][i] = c[i] / statistics.fmean(c[i - 49:i + 1]) - 1
        if i >= 199:
            out["sma200"][i] = c[i] / statistics.fmean(c[i - 199:i + 1]) - 1
            base = statistics.fmean(v[i - 199:i + 1])
            out["volr"][i] = statistics.fmean(v[i - 19:i + 1]) / base if base else None
        if i >= 252:
            out["hi52"][i] = c[i] / max(h[i - 251:i + 1])
            out[CONTROL][i] = c[i - 21] / c[i - 252] - 1
            out["mom12_7"][i] = c[i - 126] / c[i - 252] - 1
            out["rev1m"][i] = c[i] / c[i - 21] - 1
            ups = sum(1 for j in range(i - 251, i - 20) if c[j] > c[j - 1])
            out["fip"][i] = ups / 231
    return out


def nw(xs):
    return newey_west_t(xs, 2) if len(xs) > 4 else 0.0


async def main() -> int:
    async with httpx.AsyncClient(timeout=60) as client:
        histories = [await index_history.load(client, spec=s) for s in (SP500, SP400)]
        symbols = tuple(sorted(set().union(*(h.symbols_ever(LO, HI, collapse_share_classes=True)
                                             for h in histories))))
        bench = window(await fetch_bars("^GSPC", client, range_="12y"), LO, HI)
        raw, errors = await fetch_many(symbols, client, range_="12y")
    members = union_membership(histories)
    bars = {s: w for s, b in raw.items() if (w := window(b, LO, HI)) is not None and len(w) > MIN_BARS}
    feats = {s: series(b) for s, b in bars.items()}
    index = {s: {d: i for i, d in enumerate(b.dates)} for s, b in bars.items()}
    ends = [i for i in range(len(bench.dates) - 1)
            if bench.dates[i + 1].month != bench.dates[i].month
            and bench.dates[i] >= date(2016, 6, 1) and i + 1 + HOLD < len(bench.dates)]
    names = (CONTROL, *PUBLISHED, *CLASSIC)
    spread_a, spread_b = defaultdict(list), defaultdict(list)
    for e in ends:
        when, entry, exit_ = bench.dates[e], bench.dates[e + 1], bench.dates[e + 1 + HOLD]
        eligible = members(when)
        rows = []
        for s in eligible:
            ix = index.get(s)
            if ix is None or when not in ix or entry not in ix or exit_ not in ix or ix[when] < MIN_BARS:
                continue
            b = bars[s]
            fwd = b.closes[ix[exit_]] / b.closes[ix[entry]] - 1
            rows.append((fwd, {k: feats[s][k][ix[when]] for k in names}))
        if len(rows) < 200:
            continue
        mean_fwd = statistics.fmean(r[0] for r in rows)
        rows = [(f - mean_fwd, x) for f, x in rows]
        mom = sorted((x[CONTROL], f, x) for f, x in rows if x[CONTROL] is not None)
        top = [(f, x) for _, f, x in mom[len(mom) * 4 // 5:]]
        for k in names:
            scored = sorted((x[k], f) for f, x in rows if x[k] is not None)
            q = len(scored) // 5
            if q >= 20:
                spread_a[k].append((when, statistics.fmean(f for _, f in scored[-q:])
                                    - statistics.fmean(f for _, f in scored[:q])))
            if k != CONTROL:
                inside = sorted((x[k], f) for f, x in top if x[k] is not None)
                half = len(inside) // 2
                if half >= 10:
                    spread_b[k].append((when, statistics.fmean(f for _, f in inside[half:])
                                        - statistics.fmean(f for _, f in inside[:half])))
    print(f"{len(symbols)} symbols ever in S&P 500 + 400 since {LO}; {len(bars)} priced; "
          f"{len(errors)} fetch errors; {len(ends)} month-ends, 63-day forward excess\n")
    print(f"   {'indicator':<9}{'dir':>4}{'bar':>5}  {'A alone Q5-Q1':>14}{'t':>6}   "
          f"{'B inside momentum':>18}{'t':>6}{'16-20':>8}{'21-26':>8}   verdict")
    for k in names:
        a = [x for _, x in spread_a[k]]
        line = f"   {k:<9}"
        direction = PUBLISHED.get(k, 1 if k == CONTROL else 0)
        bar = T_PUBLISHED if k in PUBLISHED else T_CLASSIC
        line += f"{({1: '+', -1: '-', 0: '+/-'})[direction]:>4}{'' if k == CONTROL else bar:>5}"
        line += f"  {statistics.fmean(a) * 100:>+12.2f}pp{nw(a):>6.2f}"
        if k == CONTROL:
            print(line + "   (control)")
            continue
        b_all = [x for _, x in spread_b[k]]
        b1 = [x for d, x in spread_b[k] if d < SPLIT]
        b2 = [x for d, x in spread_b[k] if d >= SPLIT]
        mb, tb = statistics.fmean(b_all), nw(b_all)
        signed_t = tb * direction if direction else abs(tb)
        same = (statistics.fmean(b1) > 0) == (statistics.fmean(b2) > 0) == (mb > 0)
        ok = signed_t >= bar and same
        print(line + f"   {mb * 100:>+16.2f}pp{tb:>6.2f}{statistics.fmean(b1) * 100:>+7.2f}"
                     f"{statistics.fmean(b2) * 100:>+8.2f}   {'PASS' if ok else 'fail'}")
    print("\n   B = among the top momentum quintile each month, above-median minus below-median")
    print("   on the indicator; 63-day forward return net of the universe average, per month.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
