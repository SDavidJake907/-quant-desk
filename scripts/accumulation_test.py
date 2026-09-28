"""Does the morning scan's accumulation flag pick better sweeps?

    python scripts/accumulation_test.py
    python scripts/accumulation_test.py --store data/intraday

READ-ONLY research. Places no order and changes no policy.

WHERE THIS COMES FROM
`LLM-Quant-Engine/scripts/global_scan.py` runs before the open and flags names
that are "accumulating" — rising OBV while still basing — and a higher
conviction subset "ready to trend up". It produces `logs/morning_scan.pdf`.
That job had been disabled since 2026-08-21 and is now running again.

It is a DISCOVERY layer, which this scanner structurally lacks. The SMC screen
is reactive: it waits for sweep -> CHoCH -> BOS to complete on bars it already
holds, and has no concept of a name quietly accumulating beforehand. The
question is whether knowing that improves which sweeps are worth taking.

THE DEFINITION, PORTED VERBATIM
Nothing is retuned. Retuning it here would make this a test of my thresholds
rather than of their signal:

    obv_up       = obv[-1] > obv[-11] and obv[-1] > obv[-21]
    rng          = (last - min(close[-30:])) / (max(close[-30:]) - min(close[-30:])) * 100
    accumulating = obv_up and 30 <= rng <= 78 and last > sma50 * 0.97
    ready        = accumulating and 45 <= rsi <= 66 and rsi_rising

Their RSI is a SIMPLE mean of gains and losses over 14 bars, not Wilder's
recursive average. That is not the same indicator, and `rsi_series` in this repo
carries a comment about exactly that difference. It is ported as written,
because the point is to test THEIR signal.

POINT-IN-TIME, WHICH IS THE WHOLE TEST
The morning job runs at 05:00 AK, before the open, so for a setup triggering on
date D it could only have seen daily bars through D-1. This uses closes strictly
before D. Computing the flag from the full series and applying it backwards
would leak the future and manufacture a result — the error that has to be
avoided for any of this to mean anything.

WHY THIS ONE HAS A BETTER PRIOR THAN THE SIXTEEN NULLS
Sixteen ideas have failed here. Thirteen tried to rank setups against each other
and three tried to engineer exits. The two that survived — the 20M liquidity
floor at p=0.022 and the regime filter at p=0.201 — both said where the
MECHANISM applies rather than predicting anything.

This is closer to the second kind. A liquidity sweep needs someone accumulating
into it; a name being quietly bought while basing is a statement about that
condition, not a forecast. It is also from an INDEPENDENT source, written for a
different purpose, so it is not a threshold this project tuned on its own data.

The prior is still that it fails.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.backtest.setups import resolve
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history
from scanner.features.bars import fetch_many
from scanner.features.intraday import SESSION_ALIGNED
from scanner.screens.smc import scan

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
BANDS = ("ready", "accumulating", "neither")
#: Daily bars needed before the flag can be computed at all.
MIN_DAILY = 60


def simple_rsi(closes: list[float], n: int = 14) -> float:
    """Their RSI: a plain mean of gains and losses, not Wilder's."""
    if len(closes) < n + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(len(closes) - n, len(closes)):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    if losses == 0:
        return 100.0
    return round(100 - 100 / (1 + (gains / n) / (losses / n)), 1)


def classify(closes: list[float], volumes: list[float]) -> str:
    """`ready`, `accumulating`, or `neither`, from closes ending at D-1."""
    if len(closes) < MIN_DAILY:
        return "neither"
    last = closes[-1]
    sma50 = sum(closes[-50:]) / 50
    high, low = max(closes[-30:]), min(closes[-30:])
    rng = (last - low) / (high - low) * 100 if high > low else 50.0

    obv, series = 0.0, [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv += volumes[i]
        elif closes[i] < closes[i - 1]:
            obv -= volumes[i]
        series.append(obv)
    obv_up = series[-1] > series[-11] and series[-1] > series[-21]

    accumulating = obv_up and 30 <= rng <= 78 and last > sma50 * 0.97
    if not accumulating:
        return "neither"
    rsi = simple_rsi(closes)
    if 45 <= rsi <= 66 and rsi > simple_rsi(closes[:-5]):
        return "ready"
    return "accumulating"


def permutation_p(a: list[float], b: list[float], draws: int) -> float:
    if len(a) < 5 or len(b) < 5:
        return 1.0
    observed = abs(statistics.fmean(a) - statistics.fmean(b))
    pool, cut = a + b, len(a)
    rng = random.Random(0)
    hits = 0
    for _ in range(draws):
        rng.shuffle(pool)
        if abs(statistics.fmean(pool[:cut])
               - statistics.fmean(pool[cut:])) >= observed:
            hits += 1
    return hits / draws


def show(rows, label: str, min_group: int) -> list[str]:
    groups: dict[str, list[float]] = defaultdict(list)
    for name, r in rows:
        groups[name].append(r)
    print(f"\n  {BOLD}{label}{RESET}")
    usable = []
    for name in BANDS:
        values = groups.get(name, [])
        if not values:
            continue
        mean = statistics.fmean(values)
        se = (statistics.stdev(values) / math.sqrt(len(values))
              if len(values) > 1 else 0.0)
        thin = "" if len(values) >= min_group else f"  {YELLOW}thin{RESET}"
        tint = GREEN if mean > 0 else RED
        win = sum(1 for r in values if r > 0) / len(values) * 100
        print(f"    {name:<14}{tint}{mean:>+7.3f}R{RESET}  se {se:>5.3f}  "
              f"n={len(values):<5} win {win:>4.0f}%{thin}")
        if len(values) >= min_group:
            usable.append(name)
    return usable


async def main(store: str, holdout: float, draws: int, min_group: int) -> int:
    path = Path(store)
    history.STORE_DIR = path
    symbols = sorted({f.name.split("__")[0] for f in path.glob("*.json")})
    if not symbols:
        print(f"{RED}no symbols in {store}{RESET}")
        return 2

    print(f"{DIM}fetching daily bars for {len(symbols)} symbols...{RESET}")
    async with httpx.AsyncClient(timeout=60) as client:
        daily, _ = await fetch_many(tuple(symbols), client, range_="5y")
    print(f"{DIM}{len(daily)} daily series available{RESET}")

    rows = []
    for symbol in symbols:
        series = daily.get(symbol)
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if series is None or bars is None or len(bars) < 60:
            continue
        dates = list(series.dates)
        closes = list(series.closes)
        volumes = list(series.volumes)
        for setup in scan(bars, k=V.swing_k,
                          require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct,
                          min_reward_risk=V.min_reward_risk):
            if crosses_session(bars, setup.trigger_index):
                continue
            when = bars.times[setup.trigger_index].date()
            # STRICTLY BEFORE the trigger date: the morning job runs pre-open.
            cut = sum(1 for d in dates if d < when)
            if cut < MIN_DAILY:
                continue
            band = classify(closes[:cut], volumes[:cut])
            outcome = resolve(
                bars, symbol=setup.symbol, entry_index=setup.trigger_index,
                entry_price=setup.entry_price, stop_price=setup.stop_price,
                target_price=setup.target_price, max_bars=V.max_hold_bars,
            )
            if outcome:
                rows.append((setup.trigger_index / len(bars), band,
                             outcome.r_multiple))

    rows.sort(key=lambda row: row[0])
    scored = [(b, r) for _, b, r in rows]
    n = len(scored)
    if n < 60:
        print(f"{YELLOW}only {n} scored setups — too few{RESET}")
        return 0
    cut = int(n * (1 - holdout))
    print(f"\n{DIM}{n} setups scored; train {cut}, holdout {n - cut}{RESET}")
    print(f"{DIM}flag computed from daily closes STRICTLY BEFORE each trigger "
          f"date{RESET}")

    mix: dict[str, int] = defaultdict(int)
    for band, _ in scored:
        mix[band] += 1
    print(f"{DIM}mix: " + ", ".join(f"{b} {mix[b]} ({mix[b]/n*100:.0f}%)"
                                    for b in BANDS if mix[b]) + f"{RESET}")

    usable = show(scored[:cut], "TRAIN - choose here", min_group)
    show(scored[cut:], "HOLDOUT - same bands, unchanged", min_group)

    print(f"\n{BOLD}DOES IT SURVIVE?{RESET}")
    if len(usable) < 2:
        print(f"  {YELLOW}fewer than two bands reach n>={min_group} on train "
              f"- undecidable{RESET}")
        return 0

    train: dict[str, list[float]] = defaultdict(list)
    for band, r in scored[:cut]:
        train[band].append(r)
    best = max(usable, key=lambda b: statistics.fmean(train[b]))
    worst = min(usable, key=lambda b: statistics.fmean(train[b]))
    train_gap = statistics.fmean(train[best]) - statistics.fmean(train[worst])

    hold: dict[str, list[float]] = defaultdict(list)
    for band, r in scored[cut:]:
        hold[band].append(r)
    a, b = hold.get(best, []), hold.get(worst, [])
    print(f"  train picked {BOLD}{best}{RESET} over {BOLD}{worst}{RESET}  "
          f"gap {train_gap:+.3f}R")
    if len(a) < min_group or len(b) < min_group:
        print(f"  {YELLOW}holdout bands too small ({len(a)} vs {len(b)}){RESET}")
        return 0
    hold_gap = statistics.fmean(a) - statistics.fmean(b)
    p = permutation_p(list(a), list(b), draws)
    print(f"  holdout gap {hold_gap:+.3f}R  n={len(a)}/{len(b)}  p={p:.3f}")
    if (train_gap > 0) != (hold_gap > 0):
        print(f"  {RED}SIGN FLIPPED between halves - the signature of noise, "
              f"and the seventeenth time{RESET}")
    elif p < 0.05:
        print(f"  {GREEN}holds direction and clears p<0.05{RESET}")
        print(f"  {DIM}One sample, and an independent signal is easier to "
              f"believe than a tuned one. It still wants the 2018/2020/2022 "
              f"drawdowns before it gates anything live.{RESET}")
    else:
        print(f"  {DIM}same direction, not significant{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="data/intraday_alpaca")
    parser.add_argument("--holdout", type=float, default=0.333)
    parser.add_argument("--draws", type=int, default=20_000)
    parser.add_argument("--min-group", type=int, default=10)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(
        main(args.store, args.holdout, args.draws, args.min_group)))
