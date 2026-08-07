"""Does scaling out at a take-profit ladder beat holding to the target?

    python scripts/test_tp_ladder.py --index sp500

THE IDEA
Rather than one exit at the range high, sell a third at 1R, a third at 2R, and
let the last third run. Locks in something early, keeps a lottery ticket on the
rest, and frees capital for the next setup sooner.

WHY IT MIGHT NOT WORK HERE
This strategy's expectancy lives in a fat right tail. Median outcome is -1.00R
— most setups just fail — and the average is positive only because the winners
are large. Gapped targets came in at +2.98R. A ladder trims exactly the trades
that pay for all the losers, while doing nothing about the losers themselves:
a setup that goes straight to the stop never reaches the first rung, so it
loses a full -1R either way.

That asymmetry is the whole question. The ladder cannot improve the left side
of the distribution and can only shorten the right.

Measured tonight on the same data, an exit rule that fired on winners took
+1.25R trades down to +0.79R while raising the win rate from 59% to 82%. If
this ladder does the same thing it will look wonderful and earn less.

EVERY VARIANT IS PRICED
Partial exits multiply round trips, and each one crosses the spread. The AMD
stop filled 3.01 away from its price — 61 bps on one fill. So a cost per exit
leg is applied rather than assumed away.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import statistics

import httpx

from scanner.backtest.setups import resolve
from scanner.features.intraday import fetch_intraday, max_range
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}

#: Cost of crossing the spread on one exit leg, in R, at a 1.5% stop and
#: 20bps of friction. Each rung pays this; holding to target pays it once.
LEG_COST_R = 0.0020 / 0.015


def ladder_r(bars, setup, rungs, max_bars: int = 60) -> float | None:
    """R-multiple when a fraction is sold at each rung, remainder at the exit.

    Rungs are (R multiple, fraction). The stop applies to whatever is still
    open, so a trade that rises to 1R, sells a third, then reverses still
    loses on the other two thirds — which is the honest accounting and the
    reason a ladder is not free protection.
    """
    risk = setup.entry_price - setup.stop_price
    if risk <= 0:
        return None
    entry, stop = setup.entry_price, setup.stop_price
    end = min(len(bars), setup.trigger_index + 1 + max_bars)

    remaining, realised = 1.0, 0.0
    filled = [False] * len(rungs)

    for i in range(setup.trigger_index + 1, end):
        low, high, open_ = bars.lows[i], bars.highs[i], bars.opens[i]

        # A gap through the stop fills at the open, not at the stop.
        if open_ <= stop:
            return realised + remaining * ((open_ - entry) / risk) - LEG_COST_R

        # Within the bar, assume the stop is reached before any higher rung —
        # the pessimistic ordering, matching how `resolve` scores ambiguity.
        if low <= stop:
            return realised + remaining * (-1.0) - LEG_COST_R

        for n, (r_level, fraction) in enumerate(rungs):
            if filled[n] or remaining <= 0:
                continue
            price = entry + r_level * risk
            if high >= price:
                take = min(fraction, remaining)
                realised += take * (r_level - LEG_COST_R)
                remaining -= take
                filled[n] = True

        if remaining <= 1e-9:
            return realised
        if high >= setup.target_price:
            r = (setup.target_price - entry) / risk
            return realised + remaining * (r - LEG_COST_R)

    close = bars.closes[end - 1]
    return realised + remaining * ((close - entry) / risk - LEG_COST_R)


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
    best = max(rs) if rs else 0.0
    print(f"    {label:<32}{len(rs):>5} {colour}{mean:>+8.3f}R{RESET}  se {se:>5.3f}"
          f"  t {t:>+5.2f}  win {win:>5.1f}%  best {best:>+6.2f}R")


LADDERS = {
    "hold to target (baseline)": None,
    "1/2 at 1R, rest runs":      ((1.0, 0.5),),
    "1/3 at 1R, 1/3 at 2R":      ((1.0, 1 / 3), (2.0, 1 / 3)),
    "1/3 at 1R, 1/3 at 2R, 3R":  ((1.0, 1 / 3), (2.0, 1 / 3), (3.0, 1 / 3)),
    "all out at 1R":             ((1.0, 1.0),),
    "all out at 0.5R":           ((0.5, 1.0),),
}


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
        for setup in scan(bars, k=k, require_value_sweep=True,
                          min_risk_pct=1.5, min_reward_risk=1.5):
            outcome = resolve(bars, symbol=bars.symbol,
                              entry_index=setup.trigger_index,
                              entry_price=setup.entry_price,
                              stop_price=setup.stop_price,
                              target_price=setup.target_price, gap_fills=True)
            if outcome is None or outcome.r_multiple is None:
                continue
            variants = {}
            for name, rungs in LADDERS.items():
                variants[name] = (outcome.r_multiple - LEG_COST_R if rungs is None
                                  else ladder_r(bars, setup, rungs))
            if any(v is None for v in variants.values()):
                continue
            rows.append((setup.trigger_index, variants))

    rows.sort(key=lambda r: r[0])
    cut = int(len(rows) * (1 - holdout))
    for name, part in (("TRAIN", rows[:cut]), ("HOLDOUT", rows[cut:])):
        print(f"\n{BOLD}{name}{RESET} {DIM}({len(part)} trades, "
              f"{LEG_COST_R:.3f}R charged per exit leg){RESET}")
        for label in LADDERS:
            _report(label, [v[label] for _, v in part])

    print(f"\n{DIM}Median outcome is -1R: most setups fail and never reach a rung,")
    print(f"so a ladder cannot improve the left tail. Watch 'best' — that column")
    print(f"is the right tail the expectancy is actually made of.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--minutes", type=int, default=30)
    p.add_argument("--k", type=int, default=2)
    p.add_argument("--holdout", type=float, default=0.333)
    a = p.parse_args()
    asyncio.run(run(a.index, a.minutes, a.k, a.holdout))
