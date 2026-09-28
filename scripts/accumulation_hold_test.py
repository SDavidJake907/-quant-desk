"""If the morning scan finds good stocks, does BUYING them work?

    python scripts/accumulation_hold_test.py
    python scripts/accumulation_hold_test.py --horizons 5,10,21,42

READ-ONLY research. Places no order and changes no policy.

THE QUESTION THE FIRST TEST DID NOT ASK
`accumulation_test.py` asked whether the morning scan's flag picks better
SWEEPS, and the answer was no — flagged names did worse, in both halves. But
that was the wrong question to stop at. The morning scan is not a filter for a
30-minute structural entry. It is a different strategy: find names being quietly
bought, and hold them for days or weeks.

Those two screens want incompatible conditions. A sweep needs price driving
through clustered stops and reversing. Accumulation wants a calm base with
rising OBV. Finding that one does not improve the other says nothing about
whether either works alone.

So this asks the strategy's own question: buy on the flag, hold, measure.

THE DESIGN, AND WHY IT IS CROSS-SECTIONAL
A naive test — average the forward return of flagged names — would mostly
measure the market. Over any period where stocks rose, every flag looks
brilliant. So on EACH DAY this compares flagged names against unflagged names
in the same 106-name universe on that same day. Market drift is common to both
and cancels.

    edge(day) = mean forward return of flagged - mean of unflagged

Entry is the OPEN of day t, because the flag is computed from closes strictly
before day t and the morning job runs pre-open. Exit is the close of t + N.

THE AUTOCORRELATION CAVEAT, STATED UP FRONT
Overlapping holding windows are not independent observations: a 21-day return
starting today shares 20 days with one starting tomorrow. That inflates any
naive significance test. The permutation here shuffles WHOLE DAYS, preserving
each day's cross-section, which handles the cross-sectional dependence but not
the serial overlap. Treat p-values as optimistic, and weight the train/holdout
agreement more heavily than the p.

SEVENTEEN IDEAS HAVE FAILED HERE
All of them were about the sweep strategy. This is the first test of a
genuinely different mechanism, so the prior is weaker than usual in both
directions — there is no reason to expect it to work, and no reason its failure
would follow from the others.
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

from scanner.features.bars import fetch_many

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

#: The morning scan's own universe, as of global_scan.py.
UNIVERSE = (
    "AAPL MSFT NVDA GOOGL AMZN META AVGO ORCL CRM ADBE CSCO ACN IBM NOW INTC "
    "AMD QCOM TXN MU PLTR WMT COST HD NKE MCD SBUX TGT LOW PG KO PEP NFLX DIS "
    "CMCSA T VZ TSLA F GM JPM BAC WFC GS MS BLK SCHW AXP C SPGI CB JNJ LLY "
    "UNH ABBV MRK PFE TMO ABT DHR BMY AMGN GILD CVS ISRG MDT SYK XOM CVVX CVX "
    "COP SLB EOG PSX MPC OXY KMI WMB CAT BA HON GE UNP UPS LMT RTX DE MMM ADP "
    "NEE DUK SO D AEP SRE LIN APD SHW ECL NEM FCX AMT PLD SPG O CCI EQIX KTOS "
    "BX"
).split()

MIN_DAILY = 60


def simple_rsi(closes, n: int = 14) -> float:
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


def flags(closes, volumes) -> tuple[bool, bool]:
    """(accumulating, ready) from a series ending the day BEFORE entry."""
    if len(closes) < MIN_DAILY:
        return False, False
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
        return False, False
    rsi = simple_rsi(closes)
    return True, (45 <= rsi <= 66 and rsi > simple_rsi(closes[:-5]))


def sign_flip_p(values: list[float], draws: int) -> float:
    """Is the mean daily edge distinguishable from zero?"""
    if len(values) < 10:
        return 1.0
    observed = abs(statistics.fmean(values))
    rng = random.Random(0)
    hits = 0
    for _ in range(draws):
        flipped = statistics.fmean(
            [v if rng.random() < 0.5 else -v for v in values])
        if abs(flipped) >= observed:
            hits += 1
    return hits / draws


def report(label: str, edges: list[float]) -> tuple[float, float]:
    if not edges:
        print(f"    {label:<22}{YELLOW}no days{RESET}")
        return 0.0, 0.0
    mean = statistics.fmean(edges)
    se = (statistics.stdev(edges) / math.sqrt(len(edges))
          if len(edges) > 1 else 0.0)
    tint = GREEN if mean > 0 else RED
    positive = sum(1 for e in edges if e > 0) / len(edges) * 100
    print(f"    {label:<22}{tint}{mean * 100:>+8.3f}%{RESET}  se {se * 100:>6.3f}"
          f"  days={len(edges):<6} {positive:>4.0f}% of days positive")
    return mean, se


async def main(horizons: list[int], holdout: float, draws: int) -> int:
    print(f"{DIM}fetching 5y daily for {len(UNIVERSE)} morning-scan "
          f"symbols...{RESET}")
    async with httpx.AsyncClient(timeout=60) as client:
        daily, _ = await fetch_many(tuple(UNIVERSE), client, range_="5y")
    series = {s: b for s, b in daily.items() if b and len(b) > MIN_DAILY + 60}
    print(f"{DIM}{len(series)} usable series{RESET}")

    dates = sorted({d for b in series.values() for d in b.dates})
    index = {s: {d: i for i, d in enumerate(b.dates)} for s, b in series.items()}

    print(f"{DIM}entry at the OPEN of day t, flag from closes strictly before "
          f"t{RESET}")
    for horizon in horizons:
        per_day: dict[str, list[tuple[bool, bool, float]]] = defaultdict(list)
        for symbol, bars in series.items():
            closes = list(bars.closes)
            volumes = list(bars.volumes)
            opens = list(bars.opens)
            positions = index[symbol]
            for day, i in positions.items():
                if i < MIN_DAILY or i + horizon >= len(closes):
                    continue
                if opens[i] <= 0:
                    continue
                accumulating, ready = flags(closes[:i], volumes[:i])
                forward = closes[i + horizon] / opens[i] - 1.0
                per_day[day].append((accumulating, ready, forward))

        acc_edges, ready_edges = [], []
        for day, rows in per_day.items():
            flagged = [f for a, _, f in rows if a]
            plain = [f for a, _, f in rows if not a]
            if flagged and len(plain) >= 5:
                acc_edges.append(statistics.fmean(flagged)
                                 - statistics.fmean(plain))
            hot = [f for _, r, f in rows if r]
            cold = [f for _, r, f in rows if not r]
            if hot and len(cold) >= 5:
                ready_edges.append(statistics.fmean(hot)
                                   - statistics.fmean(cold))

        print(f"\n  {BOLD}hold {horizon} trading days{RESET}")
        for name, edges in (("accumulating", acc_edges), ("ready", ready_edges)):
            if len(edges) < 40:
                print(f"    {name:<22}{YELLOW}only {len(edges)} days{RESET}")
                continue
            edges_sorted = [e for _, e in
                            sorted(zip(sorted(per_day)[:len(edges)], edges))]
            cut = int(len(edges) * (1 - holdout))
            report(name, edges)
            train_mean = statistics.fmean(edges[:cut])
            hold_mean = statistics.fmean(edges[cut:])
            p = sign_flip_p(edges[cut:], draws)
            agree = (train_mean > 0) == (hold_mean > 0)
            mark = (f"{GREEN}agrees{RESET}" if agree
                    else f"{RED}SIGN FLIPPED{RESET}")
            print(f"      {'':<20}train {train_mean * 100:>+7.3f}%   "
                  f"holdout {hold_mean * 100:>+7.3f}%   p={p:.3f}   {mark}")

    print(f"\n{DIM}Overlapping windows are not independent, so p is optimistic. "
          f"Weight train/holdout agreement above it.{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--horizons", default="5,10,21,42")
    parser.add_argument("--holdout", type=float, default=0.333)
    parser.add_argument("--draws", type=int, default=20_000)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(
        [int(h) for h in args.horizons.split(",")],
        args.holdout, args.draws)))
