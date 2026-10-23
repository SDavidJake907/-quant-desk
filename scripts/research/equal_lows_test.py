"""Does sweeping a POOL of equal lows beat sweeping an isolated one?

    python scripts/equal_lows_test.py --index sp500 --holdout 0.333

THE IDEA, AND WHY IT IS TESTABLE HERE WITHOUT CHANGING ANYTHING
SMC material treats a run of roughly equal lows as the primary liquidity pool:
stops stack under a level that has held twice, so taking it out fills more
resting orders than taking out a low nobody was watching. The scanner cannot
see that today -- `swings` refuses ties by design -- so `swept_equal_lows` now
records how many near-equal lows each sweep cleared.

Nothing about the strategy changes. This PARTITIONS the same setups the
validated configuration already produces. If pools outperform isolated lows,
that is a ranking signal for the days more setups appear than there are slots,
which is exactly the constraint the portfolio simulation showed binding.

WHY A HOLDOUT IS NOT OPTIONAL
Splitting trades by a recorded property and keeping the better half is a
selection procedure. Do it once on all the data and a coin flip produces a
"signal". The split below is chronological and the threshold is not tuned:
one versus two-or-more, decided before looking.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import statistics

import httpx

from scanner.backtest.setups import STOP, TARGET, TIMEOUT, Report, resolve
from scanner.execution.policy import TradingPolicy
from scanner.features.intraday import fetch_intraday, max_range
from scanner.screens.smc import EQUAL_LEVEL_TOLERANCE_PCT, scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}

VALIDATED = TradingPolicy()


def _report(label: str, outcomes: tuple, share_of: int) -> None:
    report = Report(label=label, outcomes=outcomes)
    if report.count < 2:
        print(f"    {label:<24}{report.count:>6}  {YELLOW}too few to read{RESET}")
        return
    se = report.stdev_r / math.sqrt(report.count)
    colour = GREEN if report.expectancy > 0 else RED
    pct = report.count / share_of * 100.0 if share_of else 0.0
    print(f"    {label:<24}{report.count:>6} ({pct:4.1f}%)  "
          f"{colour}{report.expectancy:>+7.3f}R{RESET}  se {se:>5.3f}  "
          f"win {report.win_rate:>5.1f}%  "
          f"{DIM}target {report.share(TARGET):4.1f}% stop {report.share(STOP):4.1f}% "
          f"timeout {report.share(TIMEOUT):4.1f}%{RESET}")


async def main(index: str, minutes: int, k: int, holdout: float, max_bars: int,
               tolerance: float) -> None:
    range_ = max_range(minutes)
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])
        symbols = [m.symbol for m in universe]
        print(f"{DIM}fetching {len(symbols)} symbols, {minutes}m bars, {range_}{RESET}")
        frames = await asyncio.gather(
            *(fetch_intraday(s, client, minutes=minutes, range_=range_)
              for s in symbols),
            return_exceptions=True,
        )

    print(f"{DIM}policy: {VALIDATED.describe()}{RESET}")
    print(f"{DIM}equal-level band: {tolerance}% of price{RESET}")

    rows = []
    pools = []
    for bars in frames:
        if isinstance(bars, Exception) or len(bars) < 60:
            continue
        setups = scan(bars, k=k, require_value_sweep=VALIDATED.require_value_sweep,
                      min_risk_pct=VALIDATED.min_risk_pct,
                      min_reward_risk=VALIDATED.min_reward_risk,
                      equal_tolerance_pct=tolerance)
        for setup in setups:
            outcome = resolve(
                bars, symbol=bars.symbol, entry_index=setup.trigger_index,
                entry_price=setup.entry_price, stop_price=setup.stop_price,
                target_price=setup.target_price, max_bars=max_bars,
            )
            if outcome is None:
                continue
            # Position in the series, so the split is chronological across
            # symbols rather than by whichever symbol was fetched first.
            rows.append((setup.trigger_index / len(bars), setup.swept_equal_lows,
                         outcome))
            pools.append(setup.swept_equal_lows)

    if not rows:
        raise SystemExit("no resolved setups")

    rows.sort(key=lambda r: r[0])
    spread = statistics.fmean(pools)
    print(f"\n{DIM}{len(rows)} resolved trades; mean pool size "
          f"{spread:.2f} levels{RESET}")

    cut = int(len(rows) * (1 - holdout))
    for name, part in (("TRAIN", rows[:cut]), ("HOLDOUT", rows[cut:])):
        print(f"\n{BOLD}{name}{RESET} {DIM}({len(part)} trades){RESET}")
        _report("all", tuple(o for _, _, o in part), len(part))
        isolated = tuple(o for _, pool, o in part if pool <= 1)
        clustered = tuple(o for _, pool, o in part if pool >= 2)
        _report("isolated low (pool 1)", isolated, len(part))
        _report("pool of 2+", clustered, len(part))
        _report("pool of 3+",
                tuple(o for _, pool, o in part if pool >= 3), len(part))

        if len(isolated) > 1 and len(clustered) > 1:
            a = Report(label="a", outcomes=clustered)
            b = Report(label="b", outcomes=isolated)
            gap = a.expectancy - b.expectancy
            colour = GREEN if gap > 0 else RED
            print(f"    {DIM}pool minus isolated:{RESET} {colour}{gap:+.3f}R{RESET}")

    print(f"\n{YELLOW}Reading this:{RESET}")
    print(f"{DIM}  - the strategy is unchanged; this only partitions its trades{RESET}")
    print(f"{DIM}  - TRAIN is where a hypothesis is allowed to look good{RESET}")
    print(f"{DIM}  - HOLDOUT is the only number that counts, and one threshold{RESET}")
    print(f"{DIM}    was chosen before looking, not the best of several{RESET}")
    print(f"{DIM}  - 60 days of intraday history is one regime{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--minutes", type=int, default=VALIDATED.interval_minutes)
    p.add_argument("--k", type=int, default=VALIDATED.swing_k)
    p.add_argument("--holdout", type=float, default=0.333)
    p.add_argument("--max-bars", type=int, default=VALIDATED.max_hold_bars)
    p.add_argument("--tolerance", type=float, default=EQUAL_LEVEL_TOLERANCE_PCT,
                   help="how close two lows must be to hold the same stops, in %%")
    a = p.parse_args()
    asyncio.run(main(a.index, a.minutes, a.k, a.holdout, a.max_bars, a.tolerance))
