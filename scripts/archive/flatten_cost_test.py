"""What does the strategy earn WITH the Friday flatten actually modelled?

    python scripts/flatten_cost_test.py

READ-ONLY research over an accumulated store.

THE GAP THIS CLOSES
Every backtest number in this project resolves a trade on stop, target, or the
60-bar limit. `flatten.py` does something none of them model: at 15:45 ET on
Friday it closes everything at market, whatever it is worth.

`policy.py` prices that at **-0.213R per trade**, measured when expectancy was
+0.447R -- a third of the edge, painful but survivable. That measurement came
from the 96-session Yahoo store.

On the 501-symbol Alpaca store over two years, the same rules earn **+0.054R**.
If the flatten still costs anything close to -0.213R, the live strategy is
underwater and every published figure is describing a configuration that is not
the one being traded.

This is therefore the most consequential unmeasured number in the repo, and it
is cheap to get.

HOW THE FLATTEN IS MODELLED
`wind_down_from_weekday = 4` stops NEW positions from Thursday, and
`flat_before_weekend` closes open ones before the bell. So:

  - a setup triggering on Thursday or Friday would never be taken live, and is
    excluded rather than resolved
  - an open position is closed at the CLOSE of the last bar of the Friday
    inside its holding window, if it has not already hit a level

Both arms see identical setups. Only the exit rule differs, so the difference
is the flatten and nothing else.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED
from scanner.screens.smc import scan

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()


def walk(bars, setup, *, flatten: bool) -> tuple[float, str]:
    """Stop before target on every bar, matching resolve's pessimism."""
    entry, stop, target = setup.entry_price, setup.stop_price, setup.target_price
    risk = entry - stop
    first = setup.trigger_index
    last = min(first + V.max_hold_bars, len(bars) - 1)
    for i in range(first + 1, last + 1):
        open_, high, low = bars.opens[i], bars.highs[i], bars.lows[i]
        if open_ <= stop:
            return (open_ - entry) / risk, "stop"
        if open_ >= target:
            return (open_ - entry) / risk, "target"
        if low <= stop:
            return -1.0, "stop"
        if high >= target:
            return (target - entry) / risk, "target"
        if flatten and bars.times[i].weekday() == 4:
            # Last bar of this Friday inside the window: the bell.
            nxt = i + 1
            if nxt > last or bars.times[nxt].date() != bars.times[i].date():
                return (bars.closes[i] - entry) / risk, "flattened"
    return (bars.closes[last] - entry) / risk, "timeout"


def summarise(label: str, rows: list[tuple[float, str]]) -> float:
    rs = [r for r, _ in rows]
    mean = statistics.fmean(rs)
    se = statistics.stdev(rs) / len(rs) ** 0.5 if len(rs) > 1 else 0.0
    win = sum(1 for r in rs if r > 0) / len(rs) * 100
    mix: dict[str, int] = defaultdict(int)
    for _, reason in rows:
        mix[reason] += 1
    tint = GREEN if mean > 0 else RED
    parts = "  ".join(f"{k} {mix[k] / len(rows) * 100:.0f}%"
                      for k in ("target", "stop", "flattened", "timeout")
                      if mix[k])
    print(f"  {label:<26}{tint}{mean:>+7.3f}R{RESET}  se {se:.3f}  "
          f"win {win:>4.1f}%  n={len(rows)}")
    print(f"  {'':<26}{DIM}{parts}{RESET}")
    return mean


def main(store: str) -> int:
    path = Path(store)
    history.STORE_DIR = path
    symbols = sorted({f.name.split("__")[0] for f in path.glob("*.json")})

    plain, flat = [], []
    skipped_weekday = 0
    for symbol in symbols:
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        for setup in scan(bars, k=V.swing_k,
                          require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct,
                          min_reward_risk=V.min_reward_risk):
            i = setup.trigger_index
            if crosses_session(bars, i):
                continue
            if setup.entry_price - setup.stop_price <= 0:
                continue
            # Live would never open these. wind_down_from_weekday=4 is
            # FRIDAY (Mon=0), and Thursday trades again as of 536d8e7 -- an
            # earlier draft of this test excluded Thursday too and understated
            # the sample by a third.
            if bars.times[i].weekday() >= V.wind_down_from_weekday:
                skipped_weekday += 1
                continue
            plain.append(walk(bars, setup, flatten=False))
            flat.append(walk(bars, setup, flatten=True))

    if not plain:
        print(f"{RED}no setups{RESET}")
        return 2

    print(f"{DIM}{len(plain)} setups Mon-Thu; {skipped_weekday} Friday "
          f"setups excluded, because wind_down_from_weekday={V.wind_down_from_weekday} "
          f"means live never opens them{RESET}")
    print(f"{DIM}both arms see identical setups; only the exit differs{RESET}\n")

    a = summarise("as every backtest runs", plain)
    b = summarise("with the Friday flatten", flat)

    paired = [f - p for (p, _), (f, _) in zip(plain, flat)]
    cost = statistics.fmean(paired)
    se = statistics.stdev(paired) / len(paired) ** 0.5 if len(paired) > 1 else 0
    touched = sum(1 for _, reason in flat if reason == "flattened")

    print(f"\n{BOLD}THE COST OF THE FLATTEN{RESET}")
    print(f"  {cost:+.3f}R per trade  se {se:.3f}")
    print(f"  it touches {touched} of {len(flat)} trades "
          f"({touched / len(flat) * 100:.0f}%)")
    if touched:
        hit = [f for (f, r) in flat if r == "flattened"]
        print(f"  those {touched} average {statistics.fmean(hit):+.3f}R")
    print(f"  {DIM}policy.py quotes -0.213R, measured on the 96-session Yahoo "
          f"store when expectancy was +0.447R{RESET}")

    print(f"\n{BOLD}READING IT{RESET}")
    if b > 0.05:
        print(f"  {GREEN}the configuration actually traded still earns "
              f"{b:+.3f}R{RESET}")
    elif b > 0:
        print(f"  {YELLOW}{b:+.3f}R — positive, but thin enough that costs, "
              f"slippage and a bad month all matter more than the edge{RESET}")
    else:
        print(f"  {RED}{b:+.3f}R — the configuration actually traded loses "
              f"money on this sample. Every published figure describes the "
              f"arm WITHOUT the flatten, which is not what runs.{RESET}")
    print(f"  {DIM}One sample, one regime, no bear market. Not a verdict on the "
          f"strategy — a verdict on what is currently known about it.{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="data/intraday_alpaca")
    args = parser.parse_args()
    raise SystemExit(main(args.store))
