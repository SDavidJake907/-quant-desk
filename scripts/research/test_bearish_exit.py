"""Does BEARISH divergence make a better exit than waiting for stop or target?

    python scripts/test_bearish_exit.py --index sp500

THE HALF THAT WAS NOT TESTED
The first divergence test asked whether BULLISH divergence at the sweep — price
lower low, RSI higher low — predicts a better entry. It does not; the effect
flipped sign between train and holdout.

But the card describes two patterns, and this system is long only, so the
bearish one cannot be an entry signal at all. Price making a HIGHER high while
RSI makes a LOWER high says the advance is running out of buyers. For a
position already open and moving toward its target, that is an EXIT question,
and exits here are currently only three things: stop, target, 60-bar timeout.
Nothing watches momentum fail on the way up.

WHAT IS MEASURED
For every resolved setup, walk forward from entry. At each bar, if price has
made a higher high than an earlier post-entry swing high while RSI made a lower
high, close at that bar's close. Compare against the unmodified outcome.

WHY THIS COULD BE WORSE, NOT BETTER
An early exit caps winners as surely as it avoids losers. The strategy's edge
lives in a fat right tail — gapped targets came in at +2.98R — so a rule that
takes 1.2R instead of waiting for 4R can raise the win rate and still destroy
expectancy. Win rate is therefore reported beside expectancy, because a rule
that improves one while wrecking the other is the classic way an exit filter
looks good and is not.
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
from scanner.features.structure import HIGH, known_at, swings
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}


def bearish_exit(bars, setup, rsis, k: int, max_bars: int = 60):
    """Bar index at which bearish divergence first appears after entry.

    Pivots are filtered through `known_at`, so a swing high is only usable
    once it could actually have been confirmed — otherwise the exit reads a
    high that had not formed yet, and every result is fiction.
    """
    start, end = setup.trigger_index, min(len(bars), setup.trigger_index + max_bars)
    all_swings = swings(bars.highs, bars.lows, k)
    for i in range(start + 1, end):
        highs = [s for s in known_at(all_swings, i, k)
                 if s.kind == HIGH and start <= s.index < i]
        if len(highs) < 2:
            continue
        latest, earlier = highs[-1], highs[-2]
        if bars.highs[latest.index] <= bars.highs[earlier.index]:
            continue                       # not a higher high
        a, b = rsis[latest.index], rsis[earlier.index]
        if a is None or b is None or a >= b:
            continue                       # RSI did not make a lower high
        return i
    return None


def _stats(rs):
    if len(rs) < 2:
        return 0.0, 0.0, 0.0, 0.0
    mean = statistics.fmean(rs)
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    win = sum(1 for r in rs if r > 0) / len(rs) * 100
    return mean, se, (mean / se if se else 0.0), win


def _report(label, rs):
    mean, se, t, win = _stats(rs)
    colour = GREEN if t > 2 else (YELLOW if t > 1 else DIM)
    print(f"    {label:<34}{len(rs):>5} trades {colour}{mean:>+8.3f}R{RESET}"
          f"  se {se:>5.3f}  t {t:>+5.2f}   win {win:>5.1f}%")


async def run(index: str, minutes: int, k: int, holdout: float) -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])
        symbols = [m.symbol for m in universe]
        print(f"{DIM}fetching {len(symbols)} symbols, {minutes}m bars{RESET}")
        frames = await asyncio.gather(
            *(fetch_intraday(s, client, minutes=minutes, range_=max_range(minutes))
              for s in symbols), return_exceptions=True)

    rows = []      # (trigger_index, base_R, exit_R, fired)
    for bars in frames:
        if isinstance(bars, Exception) or len(bars) < 60:
            continue
        rsis = rsi_series(bars.closes)
        for setup in scan(bars, k=k, require_value_sweep=True,
                          min_risk_pct=1.5, min_reward_risk=1.5):
            outcome = resolve(bars, symbol=bars.symbol,
                              entry_index=setup.trigger_index,
                              entry_price=setup.entry_price,
                              stop_price=setup.stop_price,
                              target_price=setup.target_price, gap_fills=True)
            if outcome is None or outcome.r_multiple is None:
                continue
            base = outcome.r_multiple
            risk = setup.entry_price - setup.stop_price
            when = bearish_exit(bars, setup, rsis, k)

            # The divergence only counts if it fires BEFORE the trade ended;
            # a signal after the stop already hit changes nothing.
            if when is not None and risk > 0 and when < outcome.exit_index:
                exited = (bars.closes[when] - setup.entry_price) / risk
                rows.append((setup.trigger_index, base, exited, True))
            else:
                rows.append((setup.trigger_index, base, base, False))

    if not rows:
        raise SystemExit("no resolved setups")

    rows.sort(key=lambda r: r[0])
    cut = int(len(rows) * (1 - holdout))
    for name, part in (("TRAIN", rows[:cut]), ("HOLDOUT", rows[cut:])):
        fired = [r for r in part if r[3]]
        print(f"\n{BOLD}{name}{RESET} {DIM}({len(part)} trades, divergence fired "
              f"on {len(fired)} = {len(fired)/len(part)*100:.0f}%){RESET}")
        _report("baseline (stop/target/timeout)", [r[1] for r in part])
        _report("exit on bearish divergence", [r[2] for r in part])
        if fired:
            print(f"{DIM}    on the {len(fired)} where it fired:{RESET}")
            _report("  would have been", [r[1] for r in fired])
            _report("  exited instead at", [r[2] for r in fired])

    print(f"\n{DIM}An early exit caps winners as surely as it avoids losers.")
    print(f"Watch expectancy and win rate together: a rule that lifts the win")
    print(f"rate while cutting expectancy is taking 1R instead of 4R.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--minutes", type=int, default=30)
    p.add_argument("--k", type=int, default=2)
    p.add_argument("--holdout", type=float, default=0.333)
    a = p.parse_args()
    asyncio.run(run(a.index, a.minutes, a.k, a.holdout))
