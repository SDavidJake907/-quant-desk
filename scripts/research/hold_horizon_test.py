"""Day trade, swing, or hold? The one exit parameter nobody has varied.

    python scripts/hold_horizon_test.py

READ-ONLY research over an accumulated store.

WHY THIS IS A FAIR QUESTION AND THE CALENDAR ONES ARE NOT
Hold duration is a PARAMETER, not a prediction. Asking "which weekday pays
best" asks the data to forecast; the answer reordered completely between a
125-setup sample and an 861-setup one. Asking "how long should a position be
allowed to run" asks what the exit rule should be, and the same bars answer it
for every setting.

`max_hold_bars = 60` -- about 4.6 sessions -- has never been varied. It arrived
as a default and stayed.

WHAT IS COMPARED
Identical setups throughout. Only the cap moves, so nothing else can explain a
difference.

    same session   closed at the last bar of the day it triggered
    13 bars        one session
    26 bars        two sessions
    60 bars        FOUR AND A HALF -- the live setting
    120 bars       about nine
    240 bars       about eighteen
    uncapped       runs to stop or target, whatever it takes

THE FRIDAY FLATTEN IS MODELLED IN EVERY ARM, because it is live in all of them.
Measured at -0.012R per trade, it is nearly free, but leaving it out would
flatter the long horizons specifically -- they are the ones a weekend can reach.

WHAT TO WATCH FOR
Longer holds will almost certainly show a higher mean R, because a target is a
fixed distance and more time means more chance of reaching it. That is not
automatically better. The comparison that matters is R PER SESSION HELD: capital
in a position for eighteen sessions is capital not available for anything else,
and `max_positions = 5` makes that a real cost rather than a theoretical one.

Both are reported. They do not have to agree, and if they disagree the choice
is a preference about turnover rather than a fact about the market.

TRIAL COST, STATED
One test, seven arms. Declared trials are already near seventy and the
Bonferroni threshold sits around 0.0007, so treat any p-value here as
decoration and read the train/holdout agreement instead.
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
#: (label, bar cap, close at the end of the entry session)
ARMS = (
    ("same session", 10_000, True),
    ("13 bars  (1 day)", 13, False),
    ("26 bars  (2 days)", 26, False),
    ("60 bars  (LIVE)", 60, False),
    ("120 bars (9 days)", 120, False),
    ("240 bars (18 days)", 240, False),
    ("uncapped", 10_000, False),
)


def walk(bars, setup, *, cap: int, same_session: bool):
    """Returns (R, reason, sessions held). Stop before target, as resolve does."""
    entry, stop, target = setup.entry_price, setup.stop_price, setup.target_price
    risk = entry - stop
    first = setup.trigger_index
    last = min(first + cap, len(bars) - 1)
    day0 = bars.times[first].date()
    for i in range(first + 1, last + 1):
        open_, high, low = bars.opens[i], bars.highs[i], bars.lows[i]
        if open_ <= stop:
            return (open_ - entry) / risk, "stop", 1
        if open_ >= target:
            return (open_ - entry) / risk, "target", 1
        if low <= stop:
            return -1.0, "stop", _sessions(bars, first, i)
        if high >= target:
            return (target - entry) / risk, "target", _sessions(bars, first, i)
        nxt = i + 1
        ends_day = nxt > last or bars.times[nxt].date() != bars.times[i].date()
        if same_session and bars.times[i].date() == day0 and ends_day:
            return (bars.closes[i] - entry) / risk, "session end", 1
        # The Friday flatten is live in every arm.
        if bars.times[i].weekday() == 4 and ends_day:
            return ((bars.closes[i] - entry) / risk, "flattened",
                    _sessions(bars, first, i))
    return ((bars.closes[last] - entry) / risk, "cap",
            _sessions(bars, first, last))


def _sessions(bars, first: int, last: int) -> int:
    return len({bars.times[j].date() for j in range(first, last + 1)})


def main(store: str, holdout: float) -> int:
    path = Path(store)
    history.STORE_DIR = path
    symbols = sorted({f.name.split("__")[0] for f in path.glob("*.json")})

    found = []
    for symbol in symbols:
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        for setup in scan(bars, k=V.swing_k,
                          require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct,
                          min_reward_risk=V.min_reward_risk):
            if crosses_session(bars, setup.trigger_index):
                continue
            if setup.entry_price - setup.stop_price <= 0:
                continue
            found.append((bars, setup))
    found.sort(key=lambda row: row[1].trigger_index / len(row[0]))
    n = len(found)
    cut = int(n * (1 - holdout))
    print(f"{DIM}{n} setups, every weekday (Friday now trades); "
          f"train {cut}, holdout {n - cut}{RESET}")
    print(f"{DIM}Friday flatten modelled in every arm{RESET}\n")

    print(f"{BOLD}{'arm':<20}{'ALL':>9}{'TRAIN':>9}{'HOLDOUT':>9}"
          f"{'win':>7}{'sess':>7}{'R/sess':>9}  exits{RESET}")
    for label, cap, same in ARMS:
        rows = [walk(b, s, cap=cap, same_session=same) for b, s in found]
        rs = [r for r, _, _ in rows]
        sess = [x for _, _, x in rows]
        mean = statistics.fmean(rs)
        per = mean / statistics.fmean(sess) if statistics.fmean(sess) else 0.0
        mix: dict[str, int] = defaultdict(int)
        for _, reason, _ in rows:
            mix[reason] += 1
        top = "  ".join(f"{k} {mix[k]/n*100:.0f}%" for k in
                        ("target", "stop", "session end", "flattened", "cap")
                        if mix[k])
        tint = GREEN if mean > 0 else RED
        star = "  <-" if "LIVE" in label else ""
        print(f"  {label:<18}{tint}{mean:>+9.3f}{RESET}"
              f"{statistics.fmean(rs[:cut]):>+9.3f}"
              f"{statistics.fmean(rs[cut:]):>+9.3f}"
              f"{sum(1 for r in rs if r > 0)/n*100:>6.1f}%"
              f"{statistics.fmean(sess):>7.1f}{per:>+9.3f}  {DIM}{top}{RESET}{star}")

    print(f"\n{DIM}R/sess is R divided by sessions held — what a position costs "
          f"in slot time. With max_positions=5 that is a real constraint, not a "
          f"theoretical one.{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="data/intraday_alpaca")
    parser.add_argument("--holdout", type=float, default=0.333)
    args = parser.parse_args()
    raise SystemExit(main(args.store, args.holdout))
