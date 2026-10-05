"""Momentum Pullback Confirmation: does waiting for the retest beat buying the breakout?

    python scripts/mpc_test.py

READ-ONLY research. Changes nothing live. Uses every CPU core.

THE IDEA (brought in 2026-10-05): trend -> breakout of a defined resistance on
volume -> WAIT for a pullback that holds the old resistance -> enter on a
confirmation candle; stop under the retest low; ~2:1 target. Its core claim is
that buying the breakout candle is too early. A different setup from the live
sweep screen, so it is tested as its own strategy.

RULES, FIXED BEFORE ANY OUTCOME WAS LOOKED AT (30m session bars)
    trend         daily close > 200-day SMA and daily EMA20 > EMA50, from daily
                  closes strictly before the bar's date
    resistance    highest high of the prior 40 bars (~3 sessions)
    breakout      a bar CLOSES above resistance, volume >= 1.5x its 20-bar mean
    retest        within the next 10 bars a low comes within 0.5% of the old
                  resistance and no close goes below resistance - 0.5 x ATR(14)
    confirmation  after the retest, a bar closes above the prior bar's high;
                  entry at that close
    stop          the lowest low from the breakout to the confirmation; >= 1%
    target        entry + 2R
    exit          stop, target, Friday flatten, or 60 bars
    tradeable     price >= $5, 20-bar mean dollar volume >= $20M per bar
    control       the SAME breakouts entered at the breakout bar's close, with a
                  stop the same percentage below and a 2R target
One breakout per symbol at a time; 10bps round trip in R. Train = first
two-thirds by time; nothing here was tuned on either half.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STORE = "data/intraday_alpaca"
LOOKBACK, VOL_X, RETEST_BARS, NEAR, HOLD_ATR = 40, 1.5, 10, 0.005, 0.5
MIN_STOP, TARGET_R, COST, CAP = 0.01, 2.0, 0.0010, 60


def _ema(vals, n):
    k, e = 2 / (n + 1), vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def _trend_ok(dates, closes, day):
    cut = sum(1 for d in dates if d < day)
    if cut < 210:
        return False
    c = closes[:cut]
    return c[-1] > statistics.fmean(c[-200:]) and _ema(c[-120:], 20) > _ema(c[-120:], 50)


def _atr(bars, i, n=14):
    trs = [max(bars.highs[j] - bars.lows[j], abs(bars.highs[j] - bars.closes[j - 1]),
               abs(bars.lows[j] - bars.closes[j - 1])) for j in range(i - n + 1, i + 1)]
    return statistics.fmean(trs)


def _resolve(bars, i, entry, stop, target, last):
    risk = entry - stop
    for j in range(i + 1, last + 1):
        o, h, lo = bars.opens[j], bars.highs[j], bars.lows[j]
        if o <= stop or o >= target:
            return (o - entry) / risk
        if lo <= stop:
            return (stop - entry) / risk
        if h >= target:
            return (target - entry) / risk
        if bars.times[j].weekday() == 4 and (j == last or bars.times[j + 1].date() != bars.times[j].date()):
            return (bars.closes[j] - entry) / risk
    return (bars.closes[last] - entry) / risk


def _exit(bars, i, stop, target, last):
    """(exit price, exit index) under the same rules as _resolve."""
    for j in range(i + 1, last + 1):
        o, h, lo = bars.opens[j], bars.highs[j], bars.lows[j]
        if o <= stop or o >= target:
            return o, j
        if lo <= stop:
            return stop, j
        if h >= target:
            return target, j
        if bars.times[j].weekday() == 4 and (j == last or bars.times[j + 1].date() != bars.times[j].date()):
            return bars.closes[j], j
    return bars.closes[last], last


def _work(args):
    symbol, daily = args
    from scanner.features import history
    from scanner.features.intraday import SESSION_ALIGNED
    history.STORE_DIR = Path(STORE)
    bars = history.accumulated(symbol, 30, SESSION_ALIGNED)
    if bars is None or len(bars) < 200 or daily is None:
        return []
    dates, closes = daily
    out, i, n = [], LOOKBACK + 20, len(bars)
    while i < n - CAP - RETEST_BARS - 2:
        res = max(bars.highs[i - LOOKBACK:i])
        vol20 = statistics.fmean(bars.volumes[i - 20:i])
        dvol = statistics.fmean(bars.volumes[k] * bars.closes[k] for k in range(i - 20, i))
        if not (bars.closes[i] > res and bars.volumes[i] >= VOL_X * vol20
                and bars.closes[i] >= 5 and dvol >= 20e6
                and bars.times[i].weekday() < 4
                and _trend_ok(dates, closes, bars.times[i].date())):
            i += 1
            continue
        atr = _atr(bars, i)
        cost = lambda price, risk: COST * price / risk
        # control: buy the breakout candle
        b_entry = bars.closes[i]
        b_stop_pct = None
        # the retest path
        retest, j_end, entry_i = None, min(i + RETEST_BARS, n - CAP - 2), None
        for j in range(i + 1, j_end + 1):
            if bars.closes[j] < res - HOLD_ATR * atr:
                break                                     # failed: lost the level
            if retest is None and bars.lows[j] <= res * (1 + NEAR):
                retest = j
                continue
            if retest is not None and bars.closes[j] > bars.highs[j - 1]:
                entry_i = j
                break
        row = {"at": bars.times[i], "symbol": symbol, "retested": entry_i is not None}
        if entry_i is not None:
            entry = bars.closes[entry_i]
            stop = min(bars.lows[i:entry_i + 1])
            if entry - stop < MIN_STOP * entry:
                stop = entry * (1 - MIN_STOP)
            risk = entry - stop
            last = min(entry_i + CAP, n - 1)
            row["r_retest"] = _resolve(bars, entry_i, entry, stop, entry + TARGET_R * risk, last) - cost(entry, risk)
            px, xi = _exit(bars, entry_i, stop, entry + TARGET_R * risk, last)
            row.update({"entry": entry, "stop": stop, "exit": px, "exit_at": bars.times[xi],
                        "entry_at": bars.times[entry_i]})
            b_stop_pct = risk / entry
        pct = b_stop_pct if b_stop_pct else max(MIN_STOP, 2 * atr / b_entry)
        b_stop = b_entry * (1 - pct)
        b_risk = b_entry - b_stop
        row["r_breakout"] = _resolve(bars, i, b_entry, b_stop, b_entry + TARGET_R * b_risk,
                                     min(i + CAP, n - 1)) - cost(b_entry, b_risk)
        out.append(row)
        i = (entry_i or i) + 1 + RETEST_BARS            # one breakout at a time
    return out


def stats(v):
    if not v:
        return "n=0"
    m = statistics.fmean(v)
    se = statistics.stdev(v) / len(v) ** 0.5 if len(v) > 1 else 0
    return f"{m:+.3f}R se {se:.3f} n={len(v):<5} win {sum(x > 0 for x in v) / len(v) * 100:.0f}%"


async def main() -> int:
    from scanner.features.bars import fetch_many
    symbols = sorted({f.name.split("__")[0] for f in Path(STORE).glob("*.json")})
    async with httpx.AsyncClient(timeout=60) as client:
        daily, _ = await fetch_many(tuple(symbols), client, range_="5y")
    jobs = [(s, (list(daily[s].dates), list(daily[s].closes)) if s in daily else None) for s in symbols]
    rows = []
    with ProcessPoolExecutor() as pool:
        for chunk in pool.map(_work, jobs, chunksize=8):
            rows.extend(chunk)
    rows.sort(key=lambda r: r["at"])
    cut = len(rows) * 2 // 3
    print(f"\n{len(rows)} qualifying breakouts (trend + volume), "
          f"{sum(r['retested'] for r in rows)} retested and confirmed; train to "
          f"{rows[cut - 1]['at']:%Y-%m-%d}")
    for name, part in (("TRAIN", rows[:cut]), ("HOLDOUT", rows[cut:])):
        conf = [r for r in part if r["retested"]]
        print(f"\n  {name}")
        print(f"    buy the breakout, every breakout     {stats([r['r_breakout'] for r in part])}")
        print(f"    retest + confirmation (MPC)          {stats([r['r_retest'] for r in conf])}")
        print(f"    breakout entry, SAME breakouts as MPC {stats([r['r_breakout'] for r in conf])}")
        print(f"    breakouts that never retested        {stats([r['r_breakout'] for r in part if not r['retested']])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
