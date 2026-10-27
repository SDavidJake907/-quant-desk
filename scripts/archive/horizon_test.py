"""Does where a setup sits across 1, 7, 14 and 30 days predict its outcome?

    python scripts/horizon_test.py
    python scripts/horizon_test.py --draws 50000

READ-ONLY research over the accumulated store. It resolves historical setups
and reports. It trades nothing and changes no prompt.

THE QUESTION
A setup that triggers after a month of decline is a different thing from one
that triggers in an uptrend, and the screen does not currently care. Multi
horizon context — 24 hours, a week, a fortnight, a month — is the obvious
thing to hand a model so it can judge news against price.

`ranking.py` deliberately refuses to: "You are NOT judging the chart — you
cannot see it, and price is not your job." That boundary exists because this
project's record on context is bad: seven of nine context gates made the
held-out result worse, and yesterday's outcome memory dampened the model's
conviction even on symbols it knew nothing about.

So the boundary is not crossed on a hunch. It is crossed if, and only if, the
context predicts something — and that is measurable offline right now, on
setups already in the store, with nobody's prompt touched.

HOW A HORIZON IS MEASURED
Percentage change into the trigger bar, computed from `bars.upto(index)` so
the future cannot leak in. On 30-minute bars:

    1d  = 13 bars   one session
    7d  = 65 bars   one week
    14d = 130 bars  a fortnight
    30d = 273 bars  a month

Each is bucketed rather than used raw, because a continuous variable invites
a threshold chosen after looking, which is the exact mistake that produced
+0.429R on train and +0.014R on holdout.

SAME DISCIPLINE AS EVERY OTHER TEST HERE
Split chosen on TRAIN, applied unchanged to HOLDOUT, and a Bonferroni
threshold because four horizons are four chances to be fooled. The expected
result, stated before running: nothing survives. It has been the answer every
previous time.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import statistics
import sys
from collections import defaultdict

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.backtest.setups import resolve
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}
V = TradingPolicy()

#: Bars per horizon on a 30-minute series. 13 bars is one session.
HORIZONS = {"1d": 13, "7d": 65, "14d": 130, "30d": 273}


def bucket(change: float | None) -> str:
    """Coarse bands, fixed in advance.

    Chosen before looking at any outcome, and deliberately wide: a fine
    boundary tuned after the fact is how a noise variable starts looking
    like a signal.
    """
    if change is None:
        return "unknown"
    if change <= -10.0:
        return "down hard"
    if change <= -2.0:
        return "down"
    if change < 2.0:
        return "flat"
    if change < 10.0:
        return "up"
    return "up hard"


def horizon_labels(bars, index: int) -> dict[str, str]:
    """Where this setup sat on each horizon, as of the trigger bar only."""
    out = {}
    for name, span in HORIZONS.items():
        start = index - span
        if start < 0:
            out[name] = "unknown"
            continue
        then, now = bars.closes[start], bars.closes[index]
        out[name] = bucket(None if then <= 0 else (now - then) / then * 100.0)
    return out


def permutation_p(a: list[float], b: list[float], draws: int) -> float:
    if len(a) < 5 or len(b) < 5:
        return 1.0
    observed = abs(statistics.fmean(a) - statistics.fmean(b))
    pool, cut = a + b, len(a)
    rng = random.Random(0)
    hits = 0
    for _ in range(draws):
        rng.shuffle(pool)
        if abs(statistics.fmean(pool[:cut]) - statistics.fmean(pool[cut:])) >= observed:
            hits += 1
    return hits / draws


def report(rows, horizon: str, min_group: int) -> tuple[str, str] | None:
    groups: dict[str, list[float]] = defaultdict(list)
    for labels, r in rows:
        groups[labels[horizon]].append(r)
    usable = {k: v for k, v in groups.items()
              if len(v) >= min_group and k != "unknown"}
    print(f"\n  {BOLD}{horizon}{RESET}")
    if len(usable) < 2:
        print(f"    {YELLOW}fewer than two bands reach n>={min_group}{RESET}")
        return None
    ordered = sorted(usable.items(), key=lambda kv: -statistics.fmean(kv[1]))
    for band, values in ordered:
        mean = statistics.fmean(values)
        se = (statistics.stdev(values) / math.sqrt(len(values))
              if len(values) > 1 else 0.0)
        tint = GREEN if mean > 0 else RED
        print(f"    {band:<11}{tint}{mean:>+7.3f}R{RESET}  se {se:>5.3f}  "
              f"n={len(values)}")
    return ordered[0][0], ordered[-1][0]


async def main(index: str, holdout: float, draws: int, min_group: int) -> int:
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])

    found = []
    for member in universe:
        bars = history.accumulated(member.symbol, V.interval_minutes,
                                   SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        for setup in scan(bars, k=V.swing_k,
                          require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct,
                          min_reward_risk=V.min_reward_risk):
            if crosses_session(bars, setup.trigger_index):
                continue
            found.append((bars, setup))
    if not found:
        raise SystemExit("no setups — is the store populated?")

    found.sort(key=lambda row: row[1].trigger_index / len(row[0]))
    rows = []
    for bars, setup in found:
        outcome = resolve(
            bars, symbol=setup.symbol, entry_index=setup.trigger_index,
            entry_price=setup.entry_price, stop_price=setup.stop_price,
            target_price=setup.target_price, max_bars=V.max_hold_bars,
        )
        if outcome is None:
            continue
        rows.append((horizon_labels(bars, setup.trigger_index),
                     outcome.r_multiple))

    cut = int(len(rows) * (1 - holdout))
    train, hold = rows[:cut], rows[cut:]
    print(f"{DIM}{len(rows)} resolved setups; train {len(train)}, "
          f"holdout {len(hold)}{RESET}")
    print(f"{DIM}overall {statistics.fmean([r for _, r in rows]):+.3f}R{RESET}")

    print(f"\n{BOLD}TRAIN — choose the split here{RESET}")
    chosen = {}
    for horizon in HORIZONS:
        picked = report(train, horizon, min_group)
        if picked:
            chosen[horizon] = picked

    print(f"\n{BOLD}HOLDOUT — the same split, unchanged{RESET}")
    for horizon in HORIZONS:
        report(hold, horizon, min_group)

    threshold = 0.05 / len(HORIZONS)
    print(f"\n{BOLD}DOES ANY HORIZON SURVIVE?{RESET}")
    print(f"{DIM}  best vs worst band chosen on TRAIN, tested on HOLDOUT. "
          f"{len(HORIZONS)} questions, so the threshold is "
          f"0.05/{len(HORIZONS)} = {threshold:.3f}{RESET}")
    survived = 0
    for horizon, (best, worst) in chosen.items():
        groups: dict[str, list[float]] = defaultdict(list)
        for labels, r in hold:
            groups[labels[horizon]].append(r)
        a, b = groups.get(best, []), groups.get(worst, [])
        if len(a) < min_group or len(b) < min_group:
            print(f"    {horizon:<6}{YELLOW}holdout bands too small "
                  f"({len(a)} vs {len(b)}){RESET}")
            continue
        gap = statistics.fmean(a) - statistics.fmean(b)
        p = permutation_p(list(a), list(b), draws)
        if p < threshold:
            verdict, survived = f"{GREEN}SURVIVES{RESET}", survived + 1
        elif p < 0.05:
            verdict = f"{YELLOW}p<0.05 but not after correction{RESET}"
        else:
            verdict = f"{DIM}nothing{RESET}"
        print(f"    {horizon:<6}{best} vs {worst}: {gap:+.3f}R  "
              f"n={len(a)}/{len(b)}  p={p:.3f}  {verdict}")

    print()
    if survived:
        print(f"{GREEN}{survived} horizon(s) survived. That is a reason to "
              f"record this on every decision — and still not a reason to "
              f"put it in a prompt until the ranking itself is scored.{RESET}")
    else:
        print(f"{DIM}Nothing survived, which is what the previous nine "
              f"context tests found. Recording it costs nothing; showing it "
              f"to a model has no evidence behind it.{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--holdout", type=float, default=0.333)
    p.add_argument("--draws", type=int, default=20_000)
    p.add_argument("--min-group", type=int, default=10)
    a = p.parse_args()
    raise SystemExit(asyncio.run(
        main(a.index, a.holdout, a.draws, a.min_group)))
