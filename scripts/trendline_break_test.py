"""Does the lower-high trendline break beat random entries?

    python scripts/trendline_break_test.py --index sp500

THE PATTERN
Three descending swing highs, then a close above the newest one. Entered on the
break rather than on a retest, as drawn in the SMC TradeZone chart — and
structurally the closest thing in the pattern literature to the change of
character this project's validated strategy already uses.

WHY IT NEEDS THE SAME TEST AS EVERYTHING ELSE
The raw detector fires 4,047 times in 60 days across 500 symbols, against 135
for sweep-CHoCH-BOS. A signal that common is describing ordinary price movement
rather than a rare condition. So the question is not whether the shape exists —
it does, thousands of times over — but whether entering on it beats entering at
random with identical trade management.

THE SAME FILTERS THE VALIDATED STRATEGY USES
Minimum stop 1.5%, minimum reward-risk 1.5, no entries across a session
boundary, 60-bar time limit. Without them this would be measured against a
strategy carrying handicaps it does not, and the difference would report the
filters rather than the pattern.

TWO TARGET RULES, BECAUSE A PATTERN IS NOT A TRADE
`structural` aims at the first of the three highs — the measured move, and the
analogue of the range high SMC uses. `fixed` aims at a multiple of the stop
distance. A pattern carries no levels of its own, and the choice of levels can
easily matter more than the pattern does.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random

import httpx

from scanner.backtest.setups import (
    STOP,
    TARGET,
    TIMEOUT,
    Report,
    random_control,
    resolve,
)
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features.intraday import fetch_intraday, max_range
from scanner.features.structure import HIGH, LOW, known_at, swings
from scanner.models import Frozen
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}
P = TradingPolicy()
LOOKBACK = 60


class Break(Frozen):
    """One detected break, with the levels it implies."""

    symbol: str
    trigger_index: int
    entry_price: float
    stop_price: float
    target_price: float

    @property
    def risk(self) -> float:
        return self.entry_price - self.stop_price

    @property
    def reward_risk(self) -> float:
        if self.risk <= 0:
            return 0.0
        return (self.target_price - self.entry_price) / self.risk


def detect(bars, index, points, *, target_rule, reward_risk):
    """Three descending highs, then a close above the newest. Point-in-time."""
    visible = [s for s in known_at(points, index, P.swing_k)
               if s.index >= index - LOOKBACK]
    highs = [s for s in visible if s.kind == HIGH]
    lows = [s for s in visible if s.kind == LOW]
    if len(highs) < 3 or not lows:
        return None
    first, second, third = highs[-3], highs[-2], highs[-1]
    if not (first.price > second.price > third.price):
        return None
    if bars.closes[index] <= third.price:
        return None                       # not broken yet

    # The stop sits under the most recent low: the level the break invalidates.
    recent = [low for low in lows if low.index > second.index]
    if not recent:
        return None
    stop = min(low.price for low in recent)
    entry = bars.closes[index]
    if entry <= stop:
        return None
    target = (first.price if target_rule == "structural"
              else entry + reward_risk * (entry - stop))
    setup = Break(symbol=bars.symbol, trigger_index=index, entry_price=entry,
                  stop_price=stop, target_price=target)
    if setup.risk / entry * 100.0 < P.min_risk_pct:
        return None                       # too tight to survive friction
    if setup.reward_risk < P.min_reward_risk:
        return None
    return setup


def scan_breaks(bars, *, target_rule, reward_risk):
    """Every break in the series, one per base rather than one per bar."""
    points = swings(bars.highs, bars.lows, P.swing_k)
    found, seen = [], set()
    for index in range(LOOKBACK, len(bars)):
        setup = detect(bars, index, points, target_rule=target_rule,
                       reward_risk=reward_risk)
        if setup is None:
            continue
        key = round(setup.stop_price, 4)
        if key in seen:
            continue                      # same base re-broken, not a new trade
        seen.add(key)
        found.append(setup)
    return tuple(found)


def _report(label, outcomes):
    report = Report(label=label, outcomes=outcomes)
    if report.count < 2:
        print(f"    {label:<12}{report.count:>6}  {YELLOW}too few{RESET}")
        return
    se = report.stdev_r / math.sqrt(report.count)
    tint = GREEN if report.expectancy > 0 else RED
    print(f"    {label:<12}{report.count:>6}  {tint}{report.expectancy:>+7.3f}R{RESET}"
          f"  se {se:.3f}  win {report.win_rate:>5.1f}%"
          f"  {DIM}target {report.share(TARGET):4.1f}% stop {report.share(STOP):4.1f}%"
          f" timeout {report.share(TIMEOUT):4.1f}%{RESET}")


async def main(index, target_rule, reward_risk, holdout, draws):
    range_ = max_range(P.interval_minutes)
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])
        print(f"{DIM}fetching {len(universe)} symbols, "
              f"{P.interval_minutes}m bars{RESET}")
        frames = await asyncio.gather(
            *(fetch_intraday(m.symbol, client, minutes=P.interval_minutes,
                             range_=range_) for m in universe),
            return_exceptions=True)

    print(f"{DIM}filters: stop>={P.min_risk_pct}%  rr>={P.min_reward_risk}  "
          f"target={target_rule}  hold<={P.max_hold_bars} bars  "
          f"no session-boundary entries{RESET}")

    rows, control = [], []
    rng = random.Random(42)
    for bars in frames:
        if isinstance(bars, Exception) or len(bars) < 60:
            continue
        setups = tuple(
            s for s in scan_breaks(bars, target_rule=target_rule,
                                   reward_risk=reward_risk)
            if not crosses_session(bars, s.trigger_index))
        if not setups:
            continue
        for setup in setups:
            outcome = resolve(bars, symbol=bars.symbol,
                              entry_index=setup.trigger_index,
                              entry_price=setup.entry_price,
                              stop_price=setup.stop_price,
                              target_price=setup.target_price,
                              max_bars=P.max_hold_bars)
            if outcome is not None:
                rows.append((setup.trigger_index / len(bars), outcome))
        control.extend(random_control(bars, setups, rng,
                                      max_bars=P.max_hold_bars, draws=draws,
                                      start_index=LOOKBACK))

    if len(rows) < 4:
        raise SystemExit("not enough resolved breaks")

    rows.sort(key=lambda row: row[0])
    cut = int(len(rows) * (1 - holdout))
    print(f"\n{DIM}{len(rows)} resolved breaks; train {cut}, "
          f"holdout {len(rows) - cut}; {len(control)} random controls{RESET}")

    for name, part in (("TRAIN", rows[:cut]), ("HOLDOUT", rows[cut:])):
        print(f"\n{BOLD}{name}{RESET}")
        _report("pattern", tuple(o for _, o in part))

    print(f"\n{BOLD}RANDOM CONTROL{RESET} {DIM}(same stop and target distances, "
          f"entries drawn at random in the same names){RESET}")
    _report("random", tuple(control))

    pattern = Report(label="p", outcomes=tuple(o for _, o in rows))
    rand = Report(label="r", outcomes=tuple(control))
    if pattern.count and rand.count:
        edge = pattern.expectancy - rand.expectancy
        tint = GREEN if edge > 0 else RED
        print(f"\n    {DIM}full sample:{RESET} pattern {pattern.expectancy:+.3f}R  "
              f"random {rand.expectancy:+.3f}R  {tint}edge {edge:+.3f}R{RESET}")

    print(f"\n{YELLOW}Reading this:{RESET}")
    print(f"{DIM}  - edge against the random control is the only column that{RESET}")
    print(f"{DIM}    matters; absolute R is positive for most things in a{RESET}")
    print(f"{DIM}    rising market{RESET}")
    print(f"{DIM}  - 60 days of intraday history is one regime{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--target", choices=("structural", "fixed"), default="structural")
    p.add_argument("--reward-risk", type=float, default=2.0)
    p.add_argument("--holdout", type=float, default=1 / 3)
    p.add_argument("--draws", type=int, default=3)
    a = p.parse_args()
    asyncio.run(main(a.index, a.target, a.reward_risk, a.holdout, a.draws))
