"""Does the sweep setup only work when the market is broad? And why does the
account rise while R per trade is negative?

    python scripts/breadth_test.py                                # unseen 2018 store
    python scripts/breadth_test.py --store data/intraday_alpaca \\
        --since 2024-09-01 --until 2026-12-31                     # the 2y store

READ-ONLY research. Changes nothing live. Same baseline trades as
gate1_test.py (live gates, Mon-Thu, Friday flatten, 10bps), cached in tmp/.

PART A -- R AGAINST DOLLARS (descriptive)
Gate 1 found -0.041R/trade and an account +35.9%. Under the 20% position cap,
a trade's dollar result follows its PERCENT move, not its R: a loser costs
20% x stop%, a winner earns 20% x its gain. If winners carry wider stops than
losers, R can be negative while dollars are positive. Shown: mean % per trade,
stop % of winners and losers, and the account under equal-dollar sizing.

PART B -- MARKET BREADTH. FIXED 2026-10-05, COMMITTED BEFORE IT RAN.
Where it came from: live trades won 4 of 8 from Aug 7-20, when 6 of 11 sectors
were up on the month, and 1 of 7 from Sep 24-Oct 5, when 1 of 11 was. On the
unseen store, 2021 was the only period with positive R. DISCLOSED: that 2021
result was seen before this rule was written, so the 2018 store is not fully
clean for it; the per-period split below is the check -- breadth must help
inside 2018-19, 2020 and 2022-23, not only by finding 2021.
    breadth    for each trading date D: per GICS sector, the median 21-trading-
               day return of its members from daily closes strictly before D;
               breadth = how many of the 11 sectors have a median > 0
    rule       take a setup only when breadth >= 6 (a majority of sectors up)
    stated     breadth >= 6 resolves BETTER than breadth <= 5
    bands      0-3 / 4-5 / 6-8 / 9-11, descriptive
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, "scripts")
from gate1_test import PERIODS, STORE, account, load_trades, report, stats  # noqa: E402

MONTH, MAJORITY = 21, 6
BANDS = ((0, 3), (4, 5), (6, 8), (9, 11))


class Breadth:
    """Sectors (of 11) whose median 1-month member return is > 0, before date D."""

    def __init__(self, daily, sector_of):
        self._members = defaultdict(list)
        for symbol, series in daily.items():
            if sector_of.get(symbol):
                self._members[sector_of[symbol]].append((list(series.dates), list(series.closes)))
        self._cache: dict[date, int | None] = {}

    def at(self, when: date) -> int | None:
        if when not in self._cache:
            up = counted = 0
            for members in self._members.values():
                moves = []
                for dates, closes in members:
                    cut = bisect.bisect_left(dates, when)
                    if cut > MONTH and closes[cut - 1 - MONTH] > 0:
                        moves.append(closes[cut - 1] / closes[cut - 1 - MONTH] - 1)
                if len(moves) >= 5:
                    counted += 1
                    up += statistics.median(moves) > 0
            self._cache[when] = up if counted >= 10 else None
        return self._cache[when]


async def add_breadth(trades, symbols) -> None:
    import httpx
    from scanner.features.bars import fetch_many
    from sector_leadership_test import sectors
    async with httpx.AsyncClient(timeout=60) as client:
        daily, _ = await fetch_many(tuple(symbols), client, range_="10y")
    breadth = Breadth(daily, sectors())
    for t in trades:
        t["breadth"] = breadth.at(t["at"].date())


def dollars(trades) -> None:
    print("\n== A  R against dollars")
    pct = lambda t: (t["exit"] / t["entry"] - 1 - 0.0010) * 100
    stop = lambda t: (t["entry"] - t["stop"]) / t["entry"] * 100
    wins = [t for t in trades if t["r"] > 0]
    losses = [t for t in trades if t["r"] <= 0]
    print(f"   mean R per trade        {statistics.fmean(t['r'] for t in trades):+.3f}R")
    print(f"   mean % per trade        {statistics.fmean(pct(t) for t in trades):+.3f}%")
    print(f"   stop %   winners {statistics.fmean(stop(t) for t in wins):.2f}%   "
          f"losers {statistics.fmean(stop(t) for t in losses):.2f}%")
    print(f"   gain %   winners {statistics.fmean(pct(t) for t in wins):+.2f}%   "
          f"loss % losers {statistics.fmean(pct(t) for t in losses):+.2f}%")
    for label, lo, hi, _ in PERIODS:
        part = [t for t in trades if lo <= t["at"].date() <= hi]
        if part:
            print(f"   {label:<12}{statistics.fmean(t['r'] for t in part):+.3f}R  "
                  f"{statistics.fmean(pct(t) for t in part):+.3f}% per trade")


def main(store: str, since: date, until: date, workers: int) -> int:
    trades = load_trades(store, since, until, workers)
    symbols = sorted({t["symbol"] for t in trades})
    print(f"{len(trades)} gated setups {trades[0]['at']:%Y-%m-%d}..{trades[-1]['at']:%Y-%m-%d}")
    dollars(trades)
    asyncio.run(add_breadth(trades, symbols))
    measured = [t for t in trades if t["breadth"] is not None]
    print(f"\n== B  market breadth ({len(measured)} of {len(trades)} setups measured)")
    for lo, hi in BANDS:
        part = [t for t in measured if lo <= t["breadth"] <= hi]
        print(f"   {lo:>2}-{hi:<2} sectors up   {stats([t['r'] for t in part])}")
    broad = [t for t in measured if t["breadth"] >= MAJORITY]
    narrow = [t for t in measured if t["breadth"] < MAJORITY]
    report(f"B  breadth >= {MAJORITY} of 11 (compare: broad vs narrow)", broad,
           compare=(broad, narrow))
    print("\n   narrow-market setups by period (what the rule skips):")
    for label, lo, hi, _ in PERIODS:
        part = [t for t in narrow if lo <= t["at"].date() <= hi]
        if part:
            print(f"   {label:<12}{stats([t['r'] for t in part])}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--store", default=STORE)
    p.add_argument("--since", type=date.fromisoformat, default=date(2018, 1, 1))
    p.add_argument("--until", type=date.fromisoformat, default=date(2024, 8, 31))
    p.add_argument("--workers", type=int, default=6)
    a = p.parse_args()
    raise SystemExit(main(a.store, a.since, a.until, a.workers))
