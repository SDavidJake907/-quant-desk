"""How many positions at once, and how large may each one be?

    python scripts/slot_count_test.py --index sp500

THE TWO PARAMETERS ARE ONE DECISION
`max_positions` and `max_position_pct` cannot be chosen separately. Five slots
at a 20% cap deploys 100% of equity; ten slots at the same cap deploys 200%,
which is margin. An earlier version of this script varied only the slot count
and sized each position against full equity, so it silently assumed leverage it
never declared and reported returns nobody with a cash account could get.

This one tracks capital actually deployed. A setup is skipped when the money is
already committed, exactly as `sizing.size_position` skips it live when buying
power runs out — so the constraint that binds in the simulation is the one that
binds in the account.

WHY THE SPREAD MATTERS MORE THAN THE MEDIAN
133 trades over 76 days is a small sample and the specific subset a slot count
happens to take swings the result by tens of percentage points. So every
configuration is also run with outcomes reassigned at random across the same
timeline: same entries, same exits, same occupancy, only the pairing of what
happened to when. The spread of those runs is how much of any difference is
luck, and on this sample it is most of it.

Everything is scored at the VALIDATED holdout edge of +0.218R, not the +0.455R
this window shows, and net of round-trip costs.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics

import httpx

from scanner.backtest.setups import resolve
from scanner.execution.policy import TradingPolicy
from scanner.execution.sizing import size_position
from scanner.features.intraday import fetch_intraday, max_range
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}

P = TradingPolicy()

#: Validated out-of-sample expectancy. Every run is shifted to this.
VALIDATED_R = 0.218

#: Half spread plus slippage per side, matching backtest.costs defaults.
COST_BPS = 7.0

START_EQUITY = 100_000.0

#: (slots, position cap %). The first five deploy 100% of equity and need no
#: margin. The last is the current policy widened without touching the cap,
#: included only to show what the leverage is worth.
CONFIGS = (
    (5, 20.0),
    (8, 12.5),
    (10, 10.0),
    (15, 6.7),
    (20, 5.0),
    (10, 20.0),
)


def _walk(trades, slots: int, cap_pct: float, shift: float, seed: int | None):
    """Run the book and return (final equity, max drawdown %, trades taken).

    Open positions are held with their exit time and the dollars they occupy,
    so both the slot count and the cash actually available gate a new entry.
    """
    rows = sorted(trades, key=lambda t: t[0])
    if seed is not None:
        outcomes = [t[2] for t in rows]
        random.Random(seed).shuffle(outcomes)
        rows = [(a, b, outcomes[i], s) for i, (a, b, _, s) in enumerate(rows)]

    equity = START_EQUITY
    open_positions: list[tuple] = []          # (exit time, dollars, r, setup)
    peak, worst, taken = equity, 0.0, 0

    for entered, exited, r, setup in rows:
        # Settle everything that closed before this entry, oldest first.
        for position in sorted([p for p in open_positions if p[0] <= entered]):
            _, dollars, closed_r, closed_setup = position
            stop_frac = ((closed_setup.entry_price - closed_setup.stop_price)
                         / closed_setup.entry_price)
            cost_r = (COST_BPS * 2 / 10_000.0) / stop_frac
            equity += dollars * stop_frac * (closed_r + shift - cost_r)
            peak = max(peak, equity)
            worst = min(worst, equity / peak - 1.0)
            open_positions.remove(position)

        if len(open_positions) >= slots:
            continue
        committed = sum(p[1] for p in open_positions)
        available = max(0.0, equity - committed)
        sizing = size_position(equity, setup.entry_price, setup.stop_price,
                               risk_pct=P.risk_pct, max_position_pct=cap_pct,
                               buying_power=available)
        if sizing.shares < 1:
            continue
        open_positions.append((exited, sizing.position_dollars, r, setup))
        taken += 1

    for _, dollars, r, setup in open_positions:
        stop_frac = (setup.entry_price - setup.stop_price) / setup.entry_price
        cost_r = (COST_BPS * 2 / 10_000.0) / stop_frac
        equity += dollars * stop_frac * (r + shift - cost_r)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)

    return equity, worst * 100.0, taken


async def main(index: str, minutes: int, k: int, paths: int) -> None:
    range_ = max_range(minutes)
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])
        print(f"{DIM}fetching {len(universe)} symbols, {minutes}m bars, {range_}{RESET}")
        frames = await asyncio.gather(
            *(fetch_intraday(m.symbol, client, minutes=minutes, range_=range_)
              for m in universe), return_exceptions=True)

    trades = []
    for bars in frames:
        if isinstance(bars, Exception) or len(bars) < 60:
            continue
        for setup in scan(bars, k=k, require_value_sweep=P.require_value_sweep,
                          min_risk_pct=P.min_risk_pct,
                          min_reward_risk=P.min_reward_risk):
            outcome = resolve(bars, symbol=bars.symbol,
                              entry_index=setup.trigger_index,
                              entry_price=setup.entry_price,
                              stop_price=setup.stop_price,
                              target_price=setup.target_price,
                              max_bars=P.max_hold_bars)
            if outcome is not None:
                trades.append((bars.times[setup.trigger_index],
                               bars.times[outcome.exit_index],
                               outcome.r_multiple, setup))
    if not trades:
        raise SystemExit("no resolved setups")
    trades.sort(key=lambda t: t[0])
    span = (trades[-1][0] - trades[0][0]).days
    shift = VALIDATED_R - statistics.fmean(t[2] for t in trades)

    print(f"{DIM}{len(trades)} setups over {span} days, scored at the validated "
          f"{VALIDATED_R:+.3f}R net of {COST_BPS*2:.0f}bps{RESET}")
    print(f"{DIM}current policy is {P.max_positions} slots at "
          f"{P.max_position_pct:.0f}%{RESET}\n")
    print(f"    {'config':<22}{'deployed':>10}{'taken':>7}{'median':>9}"
          f"{'p10':>8}{'p90':>8}{'max DD':>9}")

    def annualise(equity: float) -> float:
        return ((equity / START_EQUITY) ** (365 / span) - 1) * 100

    for slots, cap in CONFIGS:
        deployed = slots * cap
        equity, dd, taken = _walk(trades, slots, cap, shift, None)
        boot = sorted(annualise(_walk(trades, slots, cap, shift, s)[0])
                      for s in range(paths))
        med, p10, p90 = boot[len(boot)//2], boot[len(boot)//10], boot[paths*9//10]
        label = f"{slots} slots x {cap:g}%"
        margin = deployed > 100.5
        tint = YELLOW if margin else GREEN
        print(f"    {label:<22}{tint}{deployed:>9.0f}%{RESET}{taken:>7}"
              f"{med:>8.1f}%{p10:>7.1f}%{p90:>7.1f}%{dd:>8.1f}%"
              f"{'  <- margin' if margin else ''}")

    print(f"\n{YELLOW}Reading this:{RESET}")
    print(f"{DIM}  - median/p10/p90 come from {paths} random reassignments of{RESET}")
    print(f"{DIM}    outcomes over the same timeline; the width is how much of{RESET}")
    print(f"{DIM}    any difference between rows is luck{RESET}")
    print(f"{DIM}  - rows above 100% deployed require a margin account, and a{RESET}")
    print(f"{DIM}    correlated fall hits leveraged books hardest -- the risk{RESET}")
    print(f"{DIM}    this 76-day window has no example of{RESET}")
    print(f"{DIM}  - the position cap is what holds effective risk near 0.4%{RESET}")
    print(f"{DIM}    against a policy asking for 1%; lowering it lowers that too{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--minutes", type=int, default=P.interval_minutes)
    p.add_argument("--k", type=int, default=P.swing_k)
    p.add_argument("--paths", type=int, default=200)
    a = p.parse_args()
    asyncio.run(main(a.index, a.minutes, a.k, a.paths))
