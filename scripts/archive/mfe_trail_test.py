"""Don't trail until the trade has proved it can run.

    python scripts/mfe_trail_test.py

READ-ONLY research over an accumulated store.

WHY THIS IS DIFFERENT FROM THE EXIT TEST THAT ALREADY FAILED
`structure_exit_test.py` tried a structural early exit and was the seventeenth
null: win rate rose 41.5% -> 48.1% and expectancy fell +0.358R -> +0.273R. Three
other measurements that night did the same thing. Every rule that made winning
more frequent cost money, because they all fired FROM THE FIRST BAR and cut the
25% of trades that reach target and average +2.383R.

"Designing Structure-Aware Exits and Maker-First Execution in Systematic
Trading" names both halves of that. Two documented failure modes for volatility
stops are volatility spikes yanking them tighter and range regimes chopping the
position. And the documented response is an MFE GATE: do not tighten into a
trail until the trade demonstrates it can run -- reaching an MFE threshold such
as 1R -- keeping early-stage risk control SEPARATE from late-stage profit
protection.

That separation is the part this system does not have. One stop currently does
two different jobs: it is the invalidation level AND the only profit protection.

THE REPO'S OWN DATA SAYS THE SAME THING
The 126 timeout trades realised +0.677R having reached a median MFE of +1.58R.
78% touched +1.0R at some point and 59% touched +1.5R. They did not fail -- they
ran out of clock while giving back a run they had already made. An MFE-gated
trail is aimed exactly there and, by construction, cannot touch a trade that
never reaches the gate.

THE ARMS
    baseline        stop / target / 60 bars          what runs today
    trail @1R       after MFE >= 1R, stop rises to (highest high - 1 x ATR)
    trail @1.5R     the same gate, set higher
    structure @1R   after MFE >= 1R, exit on a confirmed lower low instead

ATR IS WILDER'S, not a simple mean. The paper is explicit that ATR accounts for
gaps by taking the largest of (high-low), |high - prev close|, |low - prev
close|, and `rsi_series` in this repo already carries a comment about why
restarting a recursive average on a slice is wrong.

The stop only ever RISES. A trail that can loosen is not a trail.
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
from scanner.features.structure import swings
from scanner.screens.smc import LOW, scan

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
ATR_WINDOW = 14


def wilder_atr(bars, upto: int, window: int = ATR_WINDOW) -> float | None:
    """Wilder's recursive ATR at `upto`, gaps included via true range."""
    if upto < window + 1:
        return None
    ranges = []
    for i in range(1, upto + 1):
        prior = bars.closes[i - 1]
        ranges.append(max(bars.highs[i] - bars.lows[i],
                          abs(bars.highs[i] - prior),
                          abs(bars.lows[i] - prior)))
    atr = sum(ranges[:window]) / window
    for value in ranges[window:]:
        atr = (atr * (window - 1) + value) / window
    return atr


def walk(bars, setup, *, gate: float | None, mode: str, lows) -> tuple[float, str]:
    """gate=None is the baseline. mode is 'atr' or 'structure'."""
    entry, stop, target = setup.entry_price, setup.stop_price, setup.target_price
    risk = entry - stop
    first = setup.trigger_index
    last = min(first + V.max_hold_bars, len(bars) - 1)
    atr = wilder_atr(bars, first) or 0.0
    live_stop = stop
    peak = entry
    armed = False
    post_lows: list[float] = []

    for i in range(first + 1, last + 1):
        open_, high, low = bars.opens[i], bars.highs[i], bars.lows[i]
        if open_ <= live_stop:
            return (open_ - entry) / risk, "stop"
        if open_ >= target:
            return (open_ - entry) / risk, "target"
        if low <= live_stop:
            return (live_stop - entry) / risk, "trail" if armed else "stop"
        if high >= target:
            return (target - entry) / risk, "target"

        peak = max(peak, high)
        if gate is not None and not armed and (peak - entry) / risk >= gate:
            armed = True
        if armed:
            if mode == "atr" and atr:
                # RISES ONLY. A trail that can loosen is not a trail.
                live_stop = max(live_stop, peak - atr)
            elif mode == "structure":
                confirmed = [p for p in lows if first < p.index <= i - V.swing_k]
                post_lows = [p.price for p in confirmed]
                if len(post_lows) >= 2 and post_lows[-1] < post_lows[-2]:
                    return (bars.closes[i] - entry) / risk, "structure"
                if post_lows:
                    live_stop = max(live_stop, post_lows[-1])

        if bars.times[i].weekday() == 4:
            nxt = i + 1
            if nxt > last or bars.times[nxt].date() != bars.times[i].date():
                return (bars.closes[i] - entry) / risk, "flattened"
    return (bars.closes[last] - entry) / risk, "cap"


def main(store: str, holdout: float) -> int:
    path = Path(store)
    history.STORE_DIR = path
    symbols = sorted({f.name.split("__")[0] for f in path.glob("*.json")})

    found = []
    for symbol in symbols:
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        lows = [p for p in swings(bars.highs, bars.lows, V.swing_k)
                if p.kind == LOW]
        for setup in scan(bars, k=V.swing_k,
                          require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct,
                          min_reward_risk=V.min_reward_risk):
            if crosses_session(bars, setup.trigger_index):
                continue
            if setup.entry_price - setup.stop_price <= 0:
                continue
            found.append((bars, setup, lows))
    found.sort(key=lambda row: row[1].trigger_index / len(row[0]))
    n = len(found)
    cut = int(n * (1 - holdout))
    print(f"{DIM}{n} setups; train {cut}, holdout {n - cut}{RESET}")
    print(f"{DIM}the trail only ever rises; ATR is Wilder's, gaps included"
          f"{RESET}\n")

    arms = (("baseline", None, "atr"),
            ("trail @1R", 1.0, "atr"),
            ("trail @1.5R", 1.5, "atr"),
            ("structure @1R", 1.0, "structure"))
    print(f"{BOLD}{'arm':<16}{'ALL':>9}{'TRAIN':>9}{'HOLDOUT':>9}"
          f"{'win':>7}  exits{RESET}")
    for label, gate, mode in arms:
        rows = [walk(b, s, gate=gate, mode=mode, lows=l) for b, s, l in found]
        rs = [r for r, _ in rows]
        mix: dict[str, int] = defaultdict(int)
        for _, reason in rows:
            mix[reason] += 1
        mean = statistics.fmean(rs)
        tint = GREEN if mean > 0 else RED
        parts = "  ".join(f"{k} {mix[k] / n * 100:.0f}%" for k in
                          ("target", "stop", "trail", "structure",
                           "flattened", "cap") if mix[k])
        star = "  <-" if label == "baseline" else ""
        print(f"  {label:<14}{tint}{mean:>+9.3f}{RESET}"
              f"{statistics.fmean(rs[:cut]):>+9.3f}"
              f"{statistics.fmean(rs[cut:]):>+9.3f}"
              f"{sum(1 for r in rs if r > 0) / n * 100:>6.1f}%  "
              f"{DIM}{parts}{RESET}{star}")

    print(f"\n{DIM}The gate is the whole point: an arm cannot touch a trade "
          f"that never reaches it, so any difference comes only from trades "
          f"that had already run.{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="data/intraday_alpaca")
    parser.add_argument("--holdout", type=float, default=0.333)
    args = parser.parse_args()
    raise SystemExit(main(args.store, args.holdout))
