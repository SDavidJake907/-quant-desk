"""Does RSI divergence at the sweep predict a better outcome?

    python scripts/test_divergence.py --index sp500

WHY THIS IS NOT A NEW INDICATOR
The screen already requires `sweep.price < prior_low.price` — a lower low.
That is the price half of bullish divergence, enforced on every setup this
system has ever taken. The only missing half is whether RSI made a HIGHER low
at the same time, meaning the selling that produced the new low was weaker
than the selling before it.

So this is not a filter bolted on beside the setup. It is a quality measure on
the sweep the setup is already built from.

THE PROTOCOL, WHICH IS THE POINT
Four filters "improved" the holdout earlier in this project and every one was
the same artifact: cutting the sample until the survivors looked good. A rule
that keeps 16 of 75 trades will show a flattering average whether or not it
means anything.

So: split chronologically, measure on TRAIN only, and report the holdout once
beside the retention rate — because a large improvement on a small remainder
is the signature of noise, not edge. The bar to clear is not "positive". It is
"positive, on a sample big enough to distinguish from luck, in both halves".
"""

from __future__ import annotations

import argparse
import asyncio
import math
import statistics

import httpx

from scanner.backtest.setups import resolve
from scanner.features.intraday import fetch_intraday, max_range
from scanner.features.intraday_context import rsi_series
from scanner.features.structure import LOW, known_at, swings
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}


def divergence_at(bars, setup, k: int, rsis) -> float | None:
    """RSI at the sweep minus RSI at the low it took out.

    Positive means RSI made a higher low while price made a lower one —
    bullish divergence. Returns None when either pivot predates the RSI
    window, so an uncomputable case is never scored as zero.

    The prior low is re-derived exactly as `detect` derived it, using the same
    `known_at` confirmation lag. Reading a pivot the screen could not yet see
    would leak the future into a signal meant to be available at the trigger.
    """
    visible = [s for s in known_at(swings(bars.highs, bars.lows, k),
                                   setup.trigger_index, k) if s.kind == LOW]
    lows = [s for s in visible if s.index <= setup.sweep_index]
    if len(lows) < 2:
        return None
    sweep = next((s for s in lows if s.index == setup.sweep_index), None)
    if sweep is None:
        return None
    earlier = [s for s in lows if s.index < sweep.index]
    if not earlier:
        return None
    prior = earlier[-1]
    a, b = rsis[sweep.index], rsis[prior.index]
    if a is None or b is None:
        return None
    return a - b


def _stats(rs: list[float]) -> tuple[float, float, float]:
    """mean, standard error, and t — the three numbers that decide this."""
    if len(rs) < 2:
        return (statistics.fmean(rs) if rs else 0.0), 0.0, 0.0
    mean = statistics.fmean(rs)
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    return mean, se, (mean / se if se else 0.0)


def _report(label: str, rs: list[float], baseline: int) -> None:
    mean, se, t = _stats(rs)
    kept = len(rs) / baseline * 100 if baseline else 0.0
    colour = GREEN if t > 2 else (YELLOW if t > 1 else DIM)
    warn = f"  {RED}<- small remainder{RESET}" if kept < 40 else ""
    print(f"    {label:<28}{len(rs):>5} trades {colour}{mean:>+8.3f}R{RESET}"
          f"  se {se:>5.3f}  t {t:>+5.2f}   keeps {kept:>5.1f}%{warn}")


async def main(index: str, minutes: int, k: int, holdout: float) -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])
        symbols = [m.symbol for m in universe]
        print(f"{DIM}fetching {len(symbols)} symbols, {minutes}m bars{RESET}")
        frames = await asyncio.gather(
            *(fetch_intraday(s, client, minutes=minutes, range_=max_range(minutes))
              for s in symbols), return_exceptions=True)

    rows: list[tuple[int, float, float]] = []   # (trigger_index, divergence, R)
    for bars in frames:
        if isinstance(bars, Exception) or len(bars) < 60:
            continue
        rsis = rsi_series(bars.closes)
        for setup in scan(bars, k=k, require_value_sweep=True,
                          min_risk_pct=1.5, min_reward_risk=1.5):
            outcome = resolve(
                bars, symbol=bars.symbol, entry_index=setup.trigger_index,
                entry_price=setup.entry_price, stop_price=setup.stop_price,
                target_price=setup.target_price, gap_fills=True,
            )
            if outcome is None or outcome.r_multiple is None:
                continue
            gap = divergence_at(bars, setup, k, rsis)
            if gap is None:
                continue
            rows.append((setup.trigger_index, gap, outcome.r_multiple))

    if not rows:
        raise SystemExit("no resolved setups with a computable divergence")

    # Chronological split by trigger position, matching the holdout protocol.
    rows.sort(key=lambda r: r[0])
    cut = int(len(rows) * (1 - holdout))
    train, test = rows[:cut], rows[cut:]

    print(f"\n{BOLD}TRAIN{RESET} {DIM}(choose here, and only here){RESET}")
    base = [r for _, _, r in train]
    _report("all setups", base, len(base))
    for threshold in (0.0, 2.0, 5.0, 10.0):
        kept = [r for _, g, r in train if g > threshold]
        _report(f"divergence > {threshold:g}", kept, len(base))
    inverse = [r for _, g, r in train if g <= 0]
    _report("NO divergence (control)", inverse, len(base))

    print(f"\n{BOLD}HOLDOUT{RESET} {DIM}(looked at once){RESET}")
    hbase = [r for _, _, r in test]
    _report("all setups", hbase, len(hbase))
    for threshold in (0.0, 2.0, 5.0, 10.0):
        kept = [r for _, g, r in test if g > threshold]
        _report(f"divergence > {threshold:g}", kept, len(hbase))

    print(f"\n{DIM}A filter that keeps under ~40% of trades and looks better is")
    print(f"showing sample shrinkage until proven otherwise. Compare the t")
    print(f"statistic, not the mean.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--minutes", type=int, default=30)
    p.add_argument("--k", type=int, default=2)
    p.add_argument("--holdout", type=float, default=0.333)
    a = p.parse_args()
    asyncio.run(main(a.index, a.minutes, a.k, a.holdout))
