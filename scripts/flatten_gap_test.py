"""Does backtest.setups.resolve measure the strategy that actually runs?

    python scripts/flatten_gap_test.py

READ-ONLY research. Changes nothing live.

WHY
On 2026-10-04 two tests reported different baselines on the same 307 setups:
gemma_graded.py's "take all" +0.395R (via resolve) and gemma_exit_plan_test's
"standard" +0.216R (its own walk). resolve() has no Friday flatten -- it holds
through the weekend until stop, target or the bar cap -- while live closes
everything before the weekend (flat_before_weekend=True). Nearly every
backtest in the repo resolves through it.

THREE COLUMNS, SAME SETUPS
    resolve           backtest.setups.resolve, as every test uses it
    walk, no flatten  gemma_exit_plan_test's walk with the flatten removed;
                      must equal resolve if the two agree on everything else
    walk, flatten     what live does
If column 2 equals column 1, the whole gap is the weekend.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.execution.policy import TradingPolicy

from gemma_graded import collect

V = TradingPolicy()


def walk(bars, setup, *, flatten: bool) -> tuple[float, str]:
    entry, stop, target = setup.entry_price, setup.stop_price, setup.target_price
    risk = entry - stop
    first = setup.trigger_index
    last = min(first + V.max_hold_bars, len(bars) - 1)
    for i in range(first + 1, last + 1):
        o, h, lo = bars.opens[i], bars.highs[i], bars.lows[i]
        if o <= stop:
            return (o - entry) / risk, "stop"
        if o >= target:
            return (o - entry) / risk, "target"
        if lo <= stop:
            return (stop - entry) / risk, "stop"
        if h >= target:
            return (target - entry) / risk, "target"
        if flatten and bars.times[i].weekday() == 4:
            nxt = i + 1
            if nxt > last or bars.times[nxt].date() != bars.times[i].date():
                return (bars.closes[i] - entry) / risk, "flattened"
    return (bars.closes[last] - entry) / risk, "cap"


def summary(label: str, values: list[float]) -> None:
    mean = statistics.fmean(values)
    se = statistics.stdev(values) / len(values) ** 0.5
    print(f"  {label:<18}{mean:>+8.3f}R  se {se:.3f}  total {sum(values):>+8.1f}R")


def main(store: str, since: str) -> int:
    rows = collect(Path(store), date.fromisoformat(since))
    resolved = [r for _, _, _, r in rows]
    plain = [walk(b, s, flatten=False)[0] for _, b, s, _ in rows]
    flat = [walk(b, s, flatten=True) for _, b, s, _ in rows]
    print(f"\n{len(rows)} setups since {since} (Mon-Thu, ungated), policy {V.fingerprint}")
    summary("resolve", resolved)
    summary("walk, no flatten", plain)
    summary("walk, flatten", [r for r, _ in flat])
    same = sum(abs(a - b) < 1e-9 for a, b in zip(resolved, plain))
    print(f"\n  resolve == walk-no-flatten on {same}/{len(rows)} setups")
    hit = [(a, r) for a, (r, why) in zip(resolved, flat) if why == "flattened"]
    if hit:
        print(f"  flattened {len(hit)}/{len(rows)} ({len(hit) / len(rows) * 100:.0f}%): "
              f"held over the weekend they made {statistics.fmean(a for a, _ in hit):+.3f}R, "
              f"flattened {statistics.fmean(r for _, r in hit):+.3f}R")
    gap = statistics.fmean(r for r, _ in flat) - statistics.fmean(resolved)
    print(f"  flatten minus resolve: {gap:+.3f}R per trade")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--store", default="data/intraday_alpaca")
    p.add_argument("--since", default="2024-01-01")
    a = p.parse_args()
    raise SystemExit(main(a.store, a.since))
