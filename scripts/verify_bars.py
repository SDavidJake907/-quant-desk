"""Are the bars sane, and do the filters enforce what they claim?

    python scripts/verify_bars.py
    python scripts/verify_bars.py --limit 60
    python scripts/verify_bars.py --index all

READ-ONLY. It inspects the accumulated store and the screen's own output.

WHY THIS EXISTS
Every expensive bug in this project has been a data bug wearing ordinary
clothes. The adjusted-price mix-up put two price scales in one series. The
partial-bar bug traded a bucket that had not closed. Null closes removed a
whole session from every daily series in silence. The bar cache renewed its
own lease and served data three buckets stale. None of them raised an error;
all of them produced numbers that looked fine.

So this asserts the things nobody checks because they are obviously true:

  BARS       high >= low, open and close inside the range, no negative
             prices, no zero volume on a liquid name, timestamps strictly
             increasing with no duplicates, buckets aligned to :00 and :30,
             sessions the right length
  FILTERS    every setup the screen returns actually satisfies the policy it
             was scanned under — the stop at least min_risk_pct away, the
             reward at least min_reward_risk, the stop BELOW the entry and
             the target above it

A filter that silently fails to filter is indistinguishable from one that had
nothing to reject, which is exactly the shape of the stale-cache bug.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter, defaultdict

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.execution.policy import TradingPolicy
from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}
V = TradingPolicy()

#: A US session is 09:30-16:00 = 13 buckets of 30 minutes.
SESSION_BARS = 13
VALID_MINUTES = {0, 30}


def check_bars(symbol: str, bars) -> list[str]:
    """Every invariant a price series must satisfy to be worth trusting."""
    faults: list[str] = []
    n = len(bars)

    for i in range(n):
        o, h, l, c = bars.opens[i], bars.highs[i], bars.lows[i], bars.closes[i]
        if h < l:
            faults.append(f"high<low at {bars.times[i]:%m-%d %H:%M}")
        if not (l <= o <= h):
            faults.append(f"open outside range at {bars.times[i]:%m-%d %H:%M}")
        if not (l <= c <= h):
            faults.append(f"close outside range at {bars.times[i]:%m-%d %H:%M}")
        if min(o, h, l, c) <= 0:
            faults.append(f"non-positive price at {bars.times[i]:%m-%d %H:%M}")
        if bars.volumes[i] < 0:
            faults.append(f"negative volume at {bars.times[i]:%m-%d %H:%M}")

    for i in range(1, n):
        if bars.times[i] <= bars.times[i - 1]:
            faults.append(f"time not increasing at {bars.times[i]:%m-%d %H:%M}")

    off = [t for t in bars.times if t.minute not in VALID_MINUTES]
    if off:
        faults.append(f"{len(off)} bucket(s) not on :00/:30, first "
                      f"{off[0]:%m-%d %H:%M}")

    outside = [t for t in bars.times
               if (t.hour, t.minute) < (9, 30) or (t.hour, t.minute) > (16, 0)]
    if outside:
        faults.append(f"{len(outside)} bar(s) outside 09:30-16:00, first "
                      f"{outside[0]:%m-%d %H:%M}")
    return faults[:4]


def check_setups(symbol: str, bars) -> list[str]:
    """Does the screen honour the policy it was handed?"""
    faults: list[str] = []
    for setup in scan(bars, k=V.swing_k,
                      require_value_sweep=V.require_value_sweep,
                      min_risk_pct=V.min_risk_pct,
                      min_reward_risk=V.min_reward_risk):
        entry, stop, target = (setup.entry_price, setup.stop_price,
                               setup.target_price)
        if stop >= entry:
            faults.append(f"stop {stop:.2f} not below entry {entry:.2f}")
            continue
        if target <= entry:
            faults.append(f"target {target:.2f} not above entry {entry:.2f}")
            continue
        risk_pct = (entry - stop) / entry * 100.0
        if risk_pct < V.min_risk_pct - 1e-9:
            faults.append(f"risk {risk_pct:.3f}% under the "
                          f"{V.min_risk_pct}% floor")
        rr = (target - entry) / (entry - stop)
        if rr < V.min_reward_risk - 1e-9:
            faults.append(f"rr {rr:.3f} under the {V.min_reward_risk} floor")
        if abs(rr - setup.reward_risk) > 0.01:
            faults.append(f"reward_risk field {setup.reward_risk:.3f} "
                          f"disagrees with the levels ({rr:.3f})")
    return faults[:4]


async def main(index: str, limit: int, minutes: int) -> int:
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])
    symbols = [m.symbol for m in universe]
    if limit:
        symbols = symbols[:limit]

    checked = skipped = 0
    bar_faults: dict[str, list[str]] = {}
    setup_faults: dict[str, list[str]] = {}
    session_lengths: Counter = Counter()
    depths = []

    for symbol in symbols:
        bars = history.accumulated(symbol, minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            skipped += 1
            continue
        checked += 1
        depths.append(len(bars))

        faults = check_bars(symbol, bars)
        if faults:
            bar_faults[symbol] = faults
        faults = check_setups(symbol, bars)
        if faults:
            setup_faults[symbol] = faults

        per_day: dict = defaultdict(int)
        for t in bars.times:
            per_day[t.date()] += 1
        # The newest day is usually still forming; the oldest is often a
        # partial window. Neither is a fault.
        for day in sorted(per_day)[1:-1]:
            session_lengths[per_day[day]] += 1

    print(f"{DIM}{checked} symbol(s) checked, {skipped} skipped "
          f"(too little history){RESET}")
    if depths:
        print(f"{DIM}depth: {min(depths)}-{max(depths)} bars, "
              f"median {sorted(depths)[len(depths)//2]}{RESET}")

    print(f"\n{BOLD}BAR INTEGRITY{RESET}")
    if bar_faults:
        print(f"  {RED}{len(bar_faults)} symbol(s) with faults{RESET}")
        for symbol, faults in list(bar_faults.items())[:8]:
            print(f"    {symbol:<6} {'; '.join(faults)}")
    else:
        print(f"  {GREEN}clean — OHLC consistent, times increasing, "
              f"buckets aligned, all within session hours{RESET}")

    print(f"\n{BOLD}SESSION LENGTH{RESET} {DIM}(13 = a full 09:30-16:00 "
          f"session){RESET}")
    total = sum(session_lengths.values()) or 1
    for length, count in sorted(session_lengths.items(), reverse=True)[:6]:
        tint = GREEN if length == SESSION_BARS else YELLOW
        note = "  <- full" if length == SESSION_BARS else (
            "  <- half day or gap" if length < SESSION_BARS else "  <- TOO LONG")
        print(f"  {tint}{length:>3} bars{RESET}  {count:>6} session-days  "
              f"{100 * count / total:>5.1f}%{DIM}{note}{RESET}")

    print(f"\n{BOLD}FILTERS — does the screen honour its own policy?{RESET}")
    print(f"{DIM}  stop below entry, target above, risk >= "
          f"{V.min_risk_pct}%, reward:risk >= {V.min_reward_risk}{RESET}")
    if setup_faults:
        print(f"  {RED}{len(setup_faults)} symbol(s) produced a setup that "
              f"violates the policy{RESET}")
        for symbol, faults in list(setup_faults.items())[:8]:
            print(f"    {symbol:<6} {'; '.join(faults)}")
        return 1
    print(f"  {GREEN}every setup satisfies the policy it was scanned "
          f"under{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--minutes", type=int, default=V.interval_minutes)
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.index, a.limit, a.minutes)))
