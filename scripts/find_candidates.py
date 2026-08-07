"""Trend screen over the S&P 500:

    python scripts/find_candidates.py            # top 15 full passes
    python scripts/find_candidates.py --limit 100 --show 10
    python scripts/find_candidates.py --near-miss

Output is a research list, not a recommendation. Every criterion is shown so
you can disagree with any of them.
"""

from __future__ import annotations

import argparse
import asyncio

import httpx

from scanner.features import technicals
from scanner.features.bars import fetch_bars, fetch_many
from scanner.screens.trend import Candidate, evaluate, rank
from scanner.sources.constituents import load

BENCHMARK = "^GSPC"

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"


def _print(c: Candidate, sector: str) -> None:
    f = c.features
    rs = f"{c.relative_strength:+.1f}pp" if c.relative_strength is not None else "n/a"
    mom = f"{f.momentum_12_1:+.1f}%" if f.momentum_12_1 is not None else "n/a"

    print(
        f"\n{BOLD}{c.symbol:<6}{RESET} {DIM}{sector:<24}{RESET} "
        f"{f.price:>10,.2f}   "
        f"RS {GREEN if (c.relative_strength or 0) > 0 else RED}{rs:>9}{RESET}   "
        f"12-1 mom {mom:>8}   "
        f"{f.pct_from_52w_high:+.1f}% from high   "
        f"score {c.score:.1f}"
    )
    for criterion in c.criteria:
        mark = f"{GREEN}pass{RESET}" if criterion.passed else f"{RED}FAIL{RESET}"
        print(f"    {mark}  {criterion.name:<26} {DIM}{criterion.detail}{RESET}")


async def main(limit: int, show: int, near_miss: bool) -> None:
    async with httpx.AsyncClient() as client:
        universe = await load(client)
        if limit:
            universe = universe[:limit]
        sectors = {c.symbol: c.sector for c in universe}

        print(f"{DIM}fetching benchmark…{RESET}")
        benchmark = technicals.compute(await fetch_bars(BENCHMARK, client))

        print(f"{DIM}fetching {len(universe)} symbols (cached after first run)…{RESET}")
        bars, errors = await fetch_many(tuple(c.symbol for c in universe), client)

    candidates = []
    for symbol, series in bars.items():
        features = technicals.compute(series)
        if features.bars_available < 220:
            continue  # not enough history for a 200-day average
        candidates.append(
            evaluate(features, technicals.relative_strength(features, benchmark))
        )

    full = rank(tuple(candidates), require_all=True)

    bench_mom = benchmark.momentum_12_1
    print(f"\n{BOLD}TREND SCREEN{RESET}  {len(candidates)} evaluated  "
          f"{DIM}benchmark {BENCHMARK} 12-1 momentum "
          f"{bench_mom:+.1f}%{RESET}" if bench_mom is not None else "")
    print(f"{GREEN}{len(full)}{RESET} passed all 6 criteria")

    for candidate in full[:show]:
        _print(candidate, sectors.get(candidate.symbol, ""))

    if near_miss:
        misses = [c for c in rank(tuple(candidates), require_all=False)
                  if not c.passed_all and c.passed_count >= 5]
        print(f"\n{YELLOW}near misses (5 of 6):{RESET} {len(misses)}")
        for candidate in misses[:show]:
            _print(candidate, sectors.get(candidate.symbol, ""))

    if errors:
        print(f"\n{DIM}unavailable: {len(errors)}{RESET}")

    print(
        f"\n{YELLOW}These are candidates matching mechanical criteria, not "
        f"recommendations.{RESET}\n{DIM}Nothing here is backtested yet — "
        f"criteria that look sensible often have no edge.{RESET}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="cap universe size")
    parser.add_argument("--show", type=int, default=15)
    parser.add_argument("--near-miss", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.limit, args.show, args.near_miss))
