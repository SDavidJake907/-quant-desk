"""Does accumulation — rising OBV — predict a better outcome on this setup?

    python scripts/test_accumulation.py --index sp500

The morning report flags "accumulation: rising OBV while basing" as a candidate
filter. This measures whether it says anything about the setups this system
actually trades.

TWO READINGS, BECAUSE "ACCUMULATION" IS AMBIGUOUS
    slope       OBV trending up across the bars before the trigger
    divergence  OBV higher at the sweep than at the low the sweep took out

The second is the volume analogue of RSI divergence, which was measured here
and found to flip sign between train and holdout. OBV is a different quantity —
volume-weighted rather than momentum — so it gets its own test rather than an
assumption.

THE BAR TO CLEAR
Not "positive". Four filters in this project improved the holdout purely by
shrinking the sample. So: consistent sign across both halves, on a remainder
big enough to distinguish from luck. Retention is printed beside every row for
exactly that reason.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import statistics

import httpx

from scanner.backtest.setups import resolve
from scanner.features.intraday import fetch_intraday, max_range
from scanner.features.intraday_context import obv_series
from scanner.features.structure import LOW, known_at, swings
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}


def obv_slope(obv, index: int, window: int = 20) -> float | None:
    """Normalised OBV drift over the window ending at `index`.

    Divided by the mean absolute level so it compares across symbols — raw OBV
    units are volume, and a mega-cap's slope would otherwise dwarf everything.
    """
    start = index - window
    if start < 1:
        return None
    span = obv[start:index + 1]
    scale = statistics.fmean(abs(v) for v in span) or 1.0
    return (span[-1] - span[0]) / scale


def obv_divergence(bars, setup, obv, k: int) -> float | None:
    """OBV at the sweep minus OBV at the low it took out, normalised."""
    visible = [s for s in known_at(swings(bars.highs, bars.lows, k),
                                   setup.trigger_index, k) if s.kind == LOW]
    earlier = [s for s in visible if s.index < setup.sweep_index]
    if not earlier:
        return None
    prior = earlier[-1].index
    span = obv[prior:setup.sweep_index + 1]
    if len(span) < 2:
        return None
    scale = statistics.fmean(abs(v) for v in span) or 1.0
    return (obv[setup.sweep_index] - obv[prior]) / scale


def _stats(rs):
    if len(rs) < 2:
        return 0.0, 0.0, 0.0
    mean = statistics.fmean(rs)
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    return mean, se, (mean / se if se else 0.0)


def _report(label, rs, baseline):
    mean, se, t = _stats(rs)
    kept = len(rs) / baseline * 100 if baseline else 0.0
    colour = GREEN if t > 2 else (YELLOW if t > 1 else DIM)
    warn = f"  {RED}<- small remainder{RESET}" if kept < 40 else ""
    print(f"    {label:<30}{len(rs):>5} {colour}{mean:>+8.3f}R{RESET}"
          f"  se {se:>5.3f}  t {t:>+5.2f}  keeps {kept:>5.1f}%{warn}")


async def run(index: str, minutes: int, k: int, holdout: float) -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])
        symbols = [m.symbol for m in universe]
        print(f"{DIM}fetching {len(symbols)} symbols, {minutes}m bars{RESET}")
        frames = await asyncio.gather(
            *(fetch_intraday(s, client, minutes=minutes, range_=max_range(minutes))
              for s in symbols), return_exceptions=True)

    rows = []
    for bars in frames:
        if isinstance(bars, Exception) or len(bars) < 60:
            continue
        obv = obv_series(bars.closes, bars.volumes)
        if not obv:
            continue
        for setup in scan(bars, k=k, require_value_sweep=True,
                          min_risk_pct=1.5, min_reward_risk=1.5):
            outcome = resolve(bars, symbol=bars.symbol,
                              entry_index=setup.trigger_index,
                              entry_price=setup.entry_price,
                              stop_price=setup.stop_price,
                              target_price=setup.target_price, gap_fills=True)
            if outcome is None or outcome.r_multiple is None:
                continue
            slope = obv_slope(obv, setup.trigger_index)
            diverge = obv_divergence(bars, setup, obv, k)
            if slope is None or diverge is None:
                continue
            rows.append((setup.trigger_index, slope, diverge, outcome.r_multiple))

    if not rows:
        raise SystemExit("no resolved setups with computable OBV")

    rows.sort(key=lambda r: r[0])
    cut = int(len(rows) * (1 - holdout))
    for name, part in (("TRAIN", rows[:cut]), ("HOLDOUT", rows[cut:])):
        base = [r for _, _, _, r in part]
        print(f"\n{BOLD}{name}{RESET}")
        _report("all setups", base, len(base))
        for threshold in (0.0, 0.5, 1.0):
            _report(f"OBV slope > {threshold:g}",
                    [r for _, s, _, r in part if s > threshold], len(base))
        _report("OBV slope <= 0 (control)",
                [r for _, s, _, r in part if s <= 0], len(base))
        for threshold in (0.0, 0.5):
            _report(f"OBV divergence > {threshold:g}",
                    [r for _, _, d, r in part if d > threshold], len(base))

    print(f"\n{DIM}A signal that flips sign between the halves is noise finding")
    print(f"a different shape in each sample. Compare t, not the mean.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--minutes", type=int, default=30)
    p.add_argument("--k", type=int, default=2)
    p.add_argument("--holdout", type=float, default=0.333)
    a = p.parse_args()
    asyncio.run(run(a.index, a.minutes, a.k, a.holdout))
