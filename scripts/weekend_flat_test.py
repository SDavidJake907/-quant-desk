"""What does going flat before the weekend cost?

    python scripts/weekend_flat_test.py --index sp500

THE RISK BEING BOUGHT OUT OF
A stop is an instruction to the exchange, and the exchange is shut from Friday
afternoon to Monday morning. Anything that happens in between -- an earnings
pre-announcement, a deal, a geopolitical event -- arrives as a gap through the
stop, not a fill at it. `backtest.setups.resolve` already models this honestly:
a bar that OPENS below the stop fills at the open, and the loss is larger than
the position was sized for. The weekend is the longest window in which that
can happen, and the only one that recurs every week.

HOW THE RULE IS MODELLED
Exit at the close of the last bar before the weekend, at market. That reuses
the resolver's existing time-limit path -- `max_bars` is simply capped at the
number of bars remaining before Friday's close -- so a flattened trade is
scored exactly like any other timeout, with no new exit logic to get wrong.

WHAT TO EXPECT, AND WHY IT IS NOT AN ARGUMENT
Cutting winners short costs money in every strategy whose expectancy sits in a
right tail, and this project has now measured that twice. So this number will
probably be negative. That does not settle anything: the point of the rule is
not to raise expectancy, it is to remove an exposure that cannot be managed.
What the number does is put a price on that decision instead of leaving it to
feel free.
"""

from __future__ import annotations

import argparse
import asyncio
import math

import httpx

from scanner.backtest.setups import STOP, TARGET, TIMEOUT, Report, resolve
from scanner.execution.policy import TradingPolicy
from scanner.features.intraday import fetch_intraday, max_range
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}

VALIDATED = TradingPolicy()

FRIDAY = 4


def bars_until_weekend(bars, entry_index: int) -> int | None:
    """Bars from `entry_index` to the last one before the market shuts for the
    weekend, or None if the series ends first.

    Detected from bar timestamps rather than a calendar: a Friday holiday makes
    Thursday the last bar of the week, and reading the timestamps gets that
    right without knowing why the market was closed.
    """
    for i in range(entry_index, len(bars) - 1):
        here, nxt = bars.times[i], bars.times[i + 1]
        if here.weekday() == FRIDAY and nxt.date() != here.date():
            return i - entry_index
        if (nxt.date() - here.date()).days >= 3:
            return i - entry_index          # long weekend, whatever the cause
    return None


def _report(label: str, outcomes: tuple) -> None:
    report = Report(label=label, outcomes=outcomes)
    if report.count < 2:
        print(f"    {label:<26}{report.count:>5}  {YELLOW}too few to read{RESET}")
        return
    se = report.stdev_r / math.sqrt(report.count)
    tint = GREEN if report.expectancy > 0 else RED
    print(f"    {label:<26}{report.count:>5}  {tint}{report.expectancy:>+7.3f}R{RESET}"
          f"  se {se:>5.3f}  win {report.win_rate:>5.1f}%"
          f"  {DIM}target {report.share(TARGET):4.1f}% stop {report.share(STOP):4.1f}%"
          f" flat/timeout {report.share(TIMEOUT):4.1f}%{RESET}")


async def main(index: str, minutes: int, k: int, max_bars: int) -> None:
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
    print(f"{DIM}hold limit {max_bars} bars; weekend rule exits at the last "
          f"bar before the close{RESET}")

    held, flat, spanning = [], [], 0
    for bars in frames:
        if isinstance(bars, Exception) or len(bars) < 60:
            continue
        for setup in scan(bars, k=k, require_value_sweep=VALIDATED.require_value_sweep,
                          min_risk_pct=VALIDATED.min_risk_pct,
                          min_reward_risk=VALIDATED.min_reward_risk):
            i = setup.trigger_index
            args = dict(symbol=bars.symbol, entry_index=i,
                        entry_price=setup.entry_price,
                        stop_price=setup.stop_price,
                        target_price=setup.target_price)
            as_held = resolve(bars, max_bars=max_bars, **args)
            if as_held is None:
                continue
            to_weekend = bars_until_weekend(bars, i)
            if to_weekend is None:
                continue
            capped = min(max_bars, to_weekend)
            if capped < 1:
                continue                      # triggered on the final bar of the week
            as_flat = resolve(bars, max_bars=capped, **args)
            if as_flat is None:
                continue
            held.append(as_held)
            flat.append(as_flat)
            if as_held.exit_index - i > to_weekend:
                spanning += 1

    if len(held) < 2:
        raise SystemExit("no resolved setups")

    print(f"\n{DIM}{len(held)} setups; {spanning} ({spanning/len(held)*100:.0f}%) "
          f"would otherwise have been open over a weekend{RESET}\n")
    _report("hold through (current)", tuple(held))
    _report("flat before weekend", tuple(flat))
    a = Report(label="a", outcomes=tuple(held))
    b = Report(label="b", outcomes=tuple(flat))
    cost = b.expectancy - a.expectancy
    tint = GREEN if cost > 0 else RED
    print(f"\n    {DIM}cost of going flat:{RESET} {tint}{cost:+.3f}R per trade{RESET}"
          f"  {DIM}(over {len(held)} setups){RESET}")

    print(f"\n{YELLOW}Reading this:{RESET}")
    print(f"{DIM}  - a negative cost is expected: any rule that closes a winner{RESET}")
    print(f"{DIM}    early cuts the right tail this strategy lives on{RESET}")
    print(f"{DIM}  - the rule is not meant to raise expectancy. It removes an{RESET}")
    print(f"{DIM}    exposure no stop can cover, and this is its price{RESET}")
    print(f"{DIM}  - 60 days of history contains about 8 weekends, none of them{RESET}")
    print(f"{DIM}    a bad one; the tail it protects against is not in this sample{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--minutes", type=int, default=VALIDATED.interval_minutes)
    p.add_argument("--k", type=int, default=VALIDATED.swing_k)
    p.add_argument("--max-bars", type=int, default=VALIDATED.max_hold_bars)
    a = p.parse_args()
    asyncio.run(main(a.index, a.minutes, a.k, a.max_bars))
