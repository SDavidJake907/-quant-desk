"""Should the stop sit a little BELOW the higher low, by a fraction of ATR?

    python scripts/stop_buffer_test.py

READ-ONLY research. Changes nothing live.

WHERE THIS CAME FROM
Two sources agree the stop has no noise buffer: "Designing Structure-Aware
Exits" (WORKLIST §25: "invalidation = structural break PLUS a volatility
buffer so noise is not mistaken for a break") and gemma4's 2026-10-04 ATR idea,
whose valid half is exactly this. The stop sits AT the higher low, where
everyone else's stops sit -- the very level the strategy says gets swept.

ARMS, FIXED FIRST: stop = higher low - k x ATR(14, Wilder, 30m, at the
trigger), k in 0 (live), 0.25, 0.5, 1.0. Entry and target unchanged. R is
measured against each arm's own risk, so every arm risks the same 1R -- the
way risk-based sizing would trade it. Friday flatten applied (WORKLIST §29).
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED
from scanner.screens.smc import scan

from flatten_gap_test import walk
from mfe_trail_test import wilder_atr

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED = "\033[32m", "\033[31m"

V = TradingPolicy()
BUFFERS = (0.0, 0.25, 0.5, 1.0)


def paired_p(diffs, draws=20_000):
    if len(diffs) < 5:
        return 1.0
    observed, rng, hits = abs(statistics.fmean(diffs)), random.Random(0), 0
    for _ in range(draws):
        if abs(statistics.fmean([d if rng.random() < 0.5 else -d for d in diffs])) >= observed:
            hits += 1
    return hits / draws


def main(store: str, holdout: float) -> int:
    path = Path(store)
    history.STORE_DIR = path
    rows = []
    for symbol in sorted({f.name.split("__")[0] for f in path.glob("*.json")}):
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        for setup in scan(bars, k=V.swing_k, require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct, min_reward_risk=V.min_reward_risk):
            i = setup.trigger_index
            if crosses_session(bars, i) or setup.entry_price - setup.stop_price <= 0:
                continue
            if bars.times[i].weekday() >= V.wind_down_from_weekday:
                continue
            if i + V.max_hold_bars > len(bars) - 1:
                continue
            atr = wilder_atr(bars, i)
            if not atr:
                continue
            results = {}
            for k in BUFFERS:
                arm = setup.model_copy(update={"stop_price": setup.stop_price - k * atr})
                results[k] = walk(bars, arm, flatten=True)
            rows.append((bars.times[i], results))
    rows.sort(key=lambda r: r[0])
    cut = int(len(rows) * (1 - holdout))
    print(f"\n{len(rows)} setups, Friday flatten, R against each arm's own risk; "
          f"train {cut} to {rows[cut - 1][0]:%Y-%m-%d}, holdout {len(rows) - cut}")
    print(f"  {'buffer':<12}{'TRAIN':>10}{'HOLDOUT':>10}{'stopped':>10}{'target':>9}"
          f"{'vs live (holdout)':>22}")
    base_hold = [r[0.0][0] for _, r in rows[cut:]]
    for k in BUFFERS:
        train = [r[k][0] for _, r in rows[:cut]]
        hold = [r[k][0] for _, r in rows[cut:]]
        stopped = sum(r[k][1] == "stop" for _, r in rows) / len(rows) * 100
        target = sum(r[k][1] == "target" for _, r in rows) / len(rows) * 100
        if k == 0:
            vs = "(live)"
        else:
            diffs = [a - b for a, b in zip(hold, base_hold)]
            train_d = statistics.fmean(train) - statistics.fmean([r[0.0][0] for _, r in rows[:cut]])
            hold_d = statistics.fmean(diffs)
            flip = " FLIP" if (train_d > 0) != (hold_d > 0) else ""
            vs = f"{hold_d:+.3f}R p={paired_p(diffs):.3f}{flip}"
        tint = lambda x: (GREEN if x > 0 else RED) + f"{x:+.3f}" + RESET
        print(f"  {str(k) + ' x ATR':<12}{tint(statistics.fmean(train)):>19}"
              f"{tint(statistics.fmean(hold)):>19}{stopped:>9.0f}%{target:>8.0f}%{vs:>22}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--store", default="data/intraday_alpaca")
    p.add_argument("--holdout", type=float, default=0.333)
    a = p.parse_args()
    raise SystemExit(main(a.store, a.holdout))
