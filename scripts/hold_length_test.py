"""How long should a trade be allowed to stay open?

    python scripts/hold_length_test.py --index sp500

THE QUESTION BEHIND THE PARAMETER
`max_hold_bars` is 60, about 4.6 sessions on 30-minute bars, so 36% of setups
are still open over a weekend -- an exposure no stop can cover, because a stop
is an instruction to an exchange that is shut. Forcing those positions flat on
Friday was measured at -0.213R per trade, essentially the whole edge.

A shorter hold attacks the same exposure from the other side. Instead of
closing a live position mid-move, it never opens one that was going to need
three days to work. The cost, if any, shows up as targets that no longer get
reached -- which is the same right-tail truncation that has now failed twice in
this project, so the expectation going in is that shorter is worse.

DISCIPLINE, GIVEN WHAT THIS SESSION HAS ALREADY COST
This is a parameter search over a strategy that already has a validated
number, which is exactly the shape of every idea that died this week. So:
the winner is chosen on TRAIN alone, and holdout is printed for all values only
so the reader can see whether the whole surface moved or one cell got lucky.
Choosing on the holdout column would make it a training set and there is no
second one.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import statistics

import httpx

from scanner.backtest.setups import STOP, TARGET, TIMEOUT, Report, resolve
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import bars_until_weekend, crosses_session
from scanner.features.intraday import fetch_intraday, max_range
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}

VALIDATED = TradingPolicy()

#: 13 bars is one session on 30-minute bars.
SESSION_BARS = 13


def _line(label: str, outcomes: tuple, weekend_share: float, holds: list) -> None:
    report = Report(label=label, outcomes=outcomes)
    if report.count < 2:
        print(f"    {label:<16}{report.count:>5}  {YELLOW}too few{RESET}")
        return
    se = report.stdev_r / math.sqrt(report.count)
    tint = GREEN if report.expectancy > 0 else RED
    mean_hold = statistics.fmean(holds) if holds else 0.0
    print(f"    {label:<16}{report.count:>5}  {tint}{report.expectancy:>+7.3f}R{RESET}"
          f"  se {se:>5.3f}  win {report.win_rate:>5.1f}%"
          f"  {DIM}target {report.share(TARGET):4.1f}% stop {report.share(STOP):4.1f}%"
          f" timeout {report.share(TIMEOUT):4.1f}%{RESET}"
          f"  hold {mean_hold:>4.1f}  {BOLD}weekend {weekend_share:>4.0f}%{RESET}")


async def main(index: str, minutes: int, k: int, holds: tuple[int, ...],
               holdout: float) -> None:
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
    print(f"{DIM}one session = {SESSION_BARS} bars; entries across a session "
          f"boundary are excluded, matching block_overnight_entry{RESET}")

    # Collect every setup once; the hold length only changes how it resolves.
    found = []
    for bars in frames:
        if isinstance(bars, Exception) or len(bars) < 60:
            continue
        for setup in scan(bars, k=k, require_value_sweep=VALIDATED.require_value_sweep,
                          min_risk_pct=VALIDATED.min_risk_pct,
                          min_reward_risk=VALIDATED.min_reward_risk):
            if crosses_session(bars, setup.trigger_index):
                continue
            found.append((bars, setup))
    if not found:
        raise SystemExit("no setups")
    found.sort(key=lambda row: row[1].trigger_index / len(row[0]))
    cut = int(len(found) * (1 - holdout))
    splits = (("TRAIN", found[:cut]), ("HOLDOUT", found[cut:]))
    print(f"{DIM}{len(found)} setups; train {cut}, holdout {len(found) - cut}{RESET}")

    for name, part in splits:
        print(f"\n{BOLD}{name}{RESET}")
        print(f"    {DIM}{'hold cap':<16}{'n':>5}  expectancy   se      win     "
              f"outcomes                              hold  weekend{RESET}")
        for cap in holds:
            outs, spans, lengths = [], 0, []
            for bars, setup in part:
                outcome = resolve(bars, symbol=bars.symbol,
                                  entry_index=setup.trigger_index,
                                  entry_price=setup.entry_price,
                                  stop_price=setup.stop_price,
                                  target_price=setup.target_price, max_bars=cap)
                if outcome is None:
                    continue
                outs.append(outcome)
                length = outcome.exit_index - setup.trigger_index
                lengths.append(length)
                to_weekend = bars_until_weekend(bars, setup.trigger_index)
                if to_weekend is not None and length > to_weekend:
                    spans += 1
            share = spans / len(outs) * 100.0 if outs else 0.0
            label = f"{cap} ({cap/SESSION_BARS:.1f} sess)"
            _line(label, tuple(outs), share, lengths)

    print(f"\n{YELLOW}Reading this:{RESET}")
    print(f"{DIM}  - 'weekend' is the share of trades still open over a weekend;{RESET}")
    print(f"{DIM}    that is the exposure being bought out of{RESET}")
    print(f"{DIM}  - choose on TRAIN. Holdout is shown for every value only to{RESET}")
    print(f"{DIM}    reveal whether the surface moved or one cell got lucky{RESET}")
    print(f"{DIM}  - 60 days is one regime and about 8 weekends, none of them bad{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--minutes", type=int, default=VALIDATED.interval_minutes)
    p.add_argument("--k", type=int, default=VALIDATED.swing_k)
    p.add_argument("--holds", type=int, nargs="+",
                   default=[13, 20, 26, 39, 52, 60])
    p.add_argument("--holdout", type=float, default=0.333)
    a = p.parse_args()
    asyncio.run(main(a.index, a.minutes, a.k, tuple(a.holds), a.holdout))
