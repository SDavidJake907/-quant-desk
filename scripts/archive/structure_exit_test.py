"""Does the trade's own premise tell you when to leave?

    python scripts/structure_exit_test.py
    python scripts/structure_exit_test.py --store data/intraday

READ-ONLY research over an accumulated store.

THE LEAK THIS AIMS AT
Measured over 860 resolved setups on the 501-symbol Alpaca store:

    target   220  25.6%   +2.383R   median hold 19 bars
    stop     514  59.8%   -1.096R   median hold 12 bars
    timeout  126  14.7%   +0.677R   median hold 60 bars

The timeout cohort realised +0.677R having reached a median MFE of +1.58R.
78% touched +1.0R at some point and 59% touched +1.5R. They did not fail --
they ran out of clock while retracing. That is ~0.9R x 126 left on the table,
the largest identifiable leak in the ledger.

WHY A LOWER LOW AND NOT AN INDICATOR
The entry is: sweep -> HIGHER LOW -> CHoCH -> BOS. The premise is that the
higher low holds. Nothing currently re-checks it; the trade leaves on a level,
a level, or a clock.

A confirmed swing low BELOW the previous post-entry swing low says the
up-structure that justified the entry has broken -- while price may still be
well above the stop. It is a level the market drew, not a parameter: no
percentage, no ATR, nothing to tune.

This is an EXIT rule, not a ranking claim. All thirteen failed ideas here tried
to say "this setup beats that setup". The one thing that worked -- the 20M
liquidity floor -- was a statement about where the mechanism applies. This is
the same shape: it says the mechanism has stopped applying.

THE CONTROL THAT MATTERS
Variant 4 simply halves the clock. If a 30-bar limit captures most of the gain,
then 60 bars was merely too long and there is no structural story to tell. That
result would be reported, not buried.

NO LOOKAHEAD
A swing low at index j is only confirmed at j + k, so an exit triggered by it is
taken at the CLOSE of bar j + k. Stop and target are checked first on every bar,
stop before target, matching the pessimistic convention in `resolve`.
"""

from __future__ import annotations

import argparse
import random
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


def structure_break_bar(points, entry_index: int, k: int) -> int | None:
    """Bar at which a post-entry lower low is CONFIRMED, or None."""
    lows = [s for s in points if s.kind == LOW and s.index > entry_index]
    for older, newer in zip(lows, lows[1:]):
        if newer.price < older.price:
            return newer.index + k
    return None


def run(bars, setup, *, max_bars: int, break_bar: int | None) -> tuple[float, str]:
    """Walk forward. Returns (R multiple, exit reason). Stop before target."""
    entry, stop, target = setup.entry_price, setup.stop_price, setup.target_price
    risk = entry - stop
    first = setup.trigger_index
    last = min(first + max_bars, len(bars) - 1)
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
        if break_bar is not None and i >= break_bar:
            return (bars.closes[i] - entry) / risk, "structure"
    return (bars.closes[last] - entry) / risk, "timeout"


def sign_flip_p(diffs: list[float], draws: int) -> float:
    """Paired test: the same setups, so permute by flipping each pair's sign."""
    if len(diffs) < 5:
        return 1.0
    observed = abs(statistics.fmean(diffs))
    rng = random.Random(0)
    hits = 0
    for _ in range(draws):
        flipped = statistics.fmean([d if rng.random() < 0.5 else -d for d in diffs])
        if abs(flipped) >= observed:
            hits += 1
    return hits / draws


def report(label: str, rs: list[float], reasons: list[str]) -> None:
    mean = statistics.fmean(rs)
    se = statistics.stdev(rs) / len(rs) ** 0.5 if len(rs) > 1 else 0.0
    win = sum(1 for r in rs if r > 0) / len(rs) * 100
    mix: dict[str, int] = defaultdict(int)
    for reason in reasons:
        mix[reason] += 1
    tint = GREEN if mean > 0 else RED
    parts = "  ".join(f"{k} {mix[k] / len(reasons) * 100:.0f}%"
                      for k in ("target", "stop", "structure", "timeout")
                      if mix[k])
    print(f"  {label:<24}{tint}{mean:>+7.3f}R{RESET} se {se:.3f}  "
          f"win {win:>5.1f}%   {DIM}{parts}{RESET}")


def main(store: str, holdout: float, draws: int) -> int:
    history.STORE_DIR = Path(store)
    symbols = sorted({f.name.split("__")[0] for f in Path(store).glob("*.json")})
    if not symbols:
        print(f"{RED}no symbols in {store}{RESET}")
        return 2

    found = []
    for symbol in symbols:
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        points = swings(bars.highs, bars.lows, V.swing_k)
        for setup in scan(bars, k=V.swing_k,
                          require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct,
                          min_reward_risk=V.min_reward_risk):
            if crosses_session(bars, setup.trigger_index):
                continue
            if setup.entry_price - setup.stop_price <= 0:
                continue
            found.append((bars, setup, points))

    found.sort(key=lambda row: row[1].trigger_index / len(row[0]))
    variants = {
        "1 today (clock 60)": (V.max_hold_bars, False),
        "2 + lower low": (V.max_hold_bars, True),
        "3 lower low, no clock": (10_000, True),
        "4 clock 30 (control)": (30, False),
    }
    results: dict[str, list[tuple[float, str]]] = {k: [] for k in variants}
    for bars, setup, points in found:
        brk = structure_break_bar(points, setup.trigger_index, V.swing_k)
        for name, (cap, use_break) in variants.items():
            results[name].append(
                run(bars, setup, max_bars=cap,
                    break_bar=brk if use_break else None))

    n = len(found)
    cut = int(n * (1 - holdout))
    print(f"{DIM}{n} setups from {len(symbols)} symbols in {store}{RESET}")
    print(f"{DIM}train {cut}, holdout {n - cut}; stop checked before target, "
          f"lower low confirmed at +k bars{RESET}\n")

    for label, part in (("TRAIN - choose here", slice(0, cut)),
                        ("HOLDOUT - unchanged", slice(cut, n))):
        print(f"  {BOLD}{label}{RESET}")
        for name in variants:
            rows = results[name][part]
            report(name, [r for r, _ in rows], [x for _, x in rows])
        print()

    base = "1 today (clock 60)"
    print(f"{BOLD}DOES IT SURVIVE?{RESET}")
    for name in list(variants)[1:]:
        train = [results[name][i][0] - results[base][i][0] for i in range(cut)]
        hold = [results[name][i][0] - results[base][i][0] for i in range(cut, n)]
        p = sign_flip_p(hold, draws)
        flipped = (statistics.fmean(train) > 0) != (statistics.fmean(hold) > 0)
        verdict = (f"{RED}SIGN FLIPPED{RESET}" if flipped
                   else f"{GREEN}holds, p<0.05{RESET}" if p < 0.05
                   else f"{DIM}same direction, not significant{RESET}")
        print(f"  {name:<24} train {statistics.fmean(train):>+7.3f}R   "
              f"holdout {statistics.fmean(hold):>+7.3f}R  p={p:.3f}   {verdict}")
    print(f"\n{DIM}Read variant 4 first. If the control captures the gain, then "
          f"60 bars was simply too long and there is no structural story.{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="data/intraday_alpaca")
    parser.add_argument("--holdout", type=float, default=0.333)
    parser.add_argument("--draws", type=int, default=20_000)
    args = parser.parse_args()
    raise SystemExit(main(args.store, args.holdout, args.draws))
