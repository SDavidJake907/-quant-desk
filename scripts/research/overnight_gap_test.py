"""What does an overnight entry actually cost?

    python scripts/overnight_gap_test.py --index sp500

THE TRADE THIS PRICES
On 2026-08-11 the scanner sent a live order for EXPE at 05:31 AK from a setup
whose trigger bar closed at 15:30 ET the previous day -- eighteen hours and one
overnight gap earlier. It won, +3.20R, which is the worst possible outcome for
learning from it.

That entry is possible because `fresh_bars` counts BARS, not TIME. Overnight is
one bar and seventeen and a half hours. The closed-market branch of trade.py
already refuses these, with a comment saying that entering at tomorrow's open
"is a different trade at a different price with a stop that may already be
broken" -- but that guard only applies while the market is shut. Three minutes
after the open the same setup becomes tradeable and nothing objects.

WHAT IS MEASURED
Every setup is scored twice against the same stop and target:

    as backtested   entry at the close of the trigger bar
    as traded live  entry at the OPEN of the next bar

For setups triggering mid-session those two are minutes apart and the
difference is ordinary slippage. For setups triggering on a session's final
bar, the second is the next morning's open, across the gap. Splitting on that
boundary is the whole experiment.

WHY THE STOP IS NOT MOVED
The stop and target stay where the pattern put them. A gapped entry therefore
gets a different R denominator, which is precisely the harm: risk was sized
from a distance that no longer matches the price actually paid. Re-deriving
the stop from the new entry would hide the effect being measured.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import statistics

import httpx

from scanner.backtest.setups import STOP, TARGET, TIMEOUT, Report, resolve
from scanner.execution.policy import TradingPolicy
from scanner.features.intraday import fetch_intraday, max_range, session_slot
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}

VALIDATED = TradingPolicy()


def crosses_session(bars, index: int) -> bool:
    """Whether the bar AFTER `index` belongs to a later trading day.

    Read off the bar timestamps rather than a session calendar: a half day or
    an unscheduled close then counts as a boundary too, which is correct --
    what matters is that the market was shut between the two prices, not why.
    """
    if index + 1 >= len(bars):
        return False
    return bars.times[index].date() != bars.times[index + 1].date()


def _report(label: str, outcomes: tuple, colour: str = "") -> None:
    report = Report(label=label, outcomes=outcomes)
    if report.count < 2:
        print(f"    {label:<28}{report.count:>5}  {YELLOW}too few to read{RESET}")
        return
    se = report.stdev_r / math.sqrt(report.count)
    tint = colour or (GREEN if report.expectancy > 0 else RED)
    print(f"    {label:<28}{report.count:>5}  {tint}{report.expectancy:>+7.3f}R{RESET}"
          f"  se {se:>5.3f}  win {report.win_rate:>5.1f}%"
          f"  {DIM}target {report.share(TARGET):4.1f}% stop {report.share(STOP):4.1f}%"
          f" timeout {report.share(TIMEOUT):4.1f}%{RESET}")


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

    same, overnight = [], []          # (as backtested, as traded live)
    gaps = []
    for bars in frames:
        if isinstance(bars, Exception) or len(bars) < 60:
            continue
        for setup in scan(bars, k=k, require_value_sweep=VALIDATED.require_value_sweep,
                          min_risk_pct=VALIDATED.min_risk_pct,
                          min_reward_risk=VALIDATED.min_reward_risk):
            i = setup.trigger_index
            if i + 1 >= len(bars):
                continue
            book = resolve(bars, symbol=bars.symbol, entry_index=i,
                           entry_price=setup.entry_price,
                           stop_price=setup.stop_price,
                           target_price=setup.target_price, max_bars=max_bars)
            # As actually traded: filled at the next bar's open, same levels.
            live_entry = bars.opens[i + 1]
            live = resolve(bars, symbol=bars.symbol, entry_index=i + 1,
                           entry_price=live_entry,
                           stop_price=setup.stop_price,
                           target_price=setup.target_price, max_bars=max_bars)
            if book is None or live is None:
                continue
            slip = (live_entry - setup.entry_price) / setup.entry_price * 100.0
            row = (book, live, slip)
            if crosses_session(bars, i):
                overnight.append(row)
            else:
                same.append(row)
            gaps.append((crosses_session(bars, i), slip))

    if not same and not overnight:
        raise SystemExit("no resolved setups")

    print(f"\n{BOLD}ENTRY SLIPPAGE{RESET} {DIM}(next open vs trigger close, "
          f"% of entry price){RESET}")
    for label, rows in (("same session", same), ("across the close", overnight)):
        if len(rows) < 2:
            print(f"    {label:<28}{len(rows):>5}  {YELLOW}too few{RESET}")
            continue
        slips = [r[2] for r in rows]
        adverse = sum(1 for s in slips if s > 0) / len(slips) * 100.0
        print(f"    {label:<28}{len(rows):>5}  mean {statistics.fmean(slips):>+6.3f}%"
              f"  median {statistics.median(slips):>+6.3f}%"
              f"  worst {max(slips):>+6.2f}%  {DIM}paid up {adverse:.0f}% of the time{RESET}")

    for label, rows in (("SAME SESSION", same), ("ACROSS THE CLOSE", overnight)):
        print(f"\n{BOLD}{label}{RESET} {DIM}({len(rows)} setups){RESET}")
        _report("as backtested (close)", tuple(r[0] for r in rows))
        _report("as traded (next open)", tuple(r[1] for r in rows))
        if len(rows) >= 2:
            a = Report(label="a", outcomes=tuple(r[0] for r in rows))
            b = Report(label="b", outcomes=tuple(r[1] for r in rows))
            cost = b.expectancy - a.expectancy
            tint = GREEN if cost > 0 else RED
            print(f"    {DIM}cost of entering at the next open:{RESET} "
                  f"{tint}{cost:+.3f}R{RESET}")

    print(f"\n{YELLOW}Reading this:{RESET}")
    print(f"{DIM}  - 'same session' is the ordinary slippage the live path already{RESET}")
    print(f"{DIM}    pays; it is the control, not the finding{RESET}")
    print(f"{DIM}  - 'across the close' is the entry fresh_bars cannot distinguish{RESET}")
    print(f"{DIM}  - the stop and target are unchanged in both, because that is{RESET}")
    print(f"{DIM}    what the live system does -- the R denominator is the harm{RESET}")
    print(f"{DIM}  - 60 days of intraday history is one regime{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--minutes", type=int, default=VALIDATED.interval_minutes)
    p.add_argument("--k", type=int, default=VALIDATED.swing_k)
    p.add_argument("--max-bars", type=int, default=VALIDATED.max_hold_bars)
    a = p.parse_args()
    asyncio.run(main(a.index, a.minutes, a.k, a.max_bars))
