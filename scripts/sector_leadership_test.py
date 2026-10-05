"""Does a sweep in a leading sector resolve better than one in a lagging sector?

    python scripts/sector_leadership_test.py

READ-ONLY research over an accumulated store. Changes nothing live.

WHERE THIS CAME FROM
The 2026-10-04 morning scan against 2026-07-14's: one sector of eleven up on
the month (Technology +8.8%) against six, and 29% of names in an uptrend
against 49%. Live trades split the same way -- IT +1.45R (3/5 won),
everything else -0.19R (2/10). Fifteen trades is an anecdote, so this asks
the backtest.

HOW IT DIFFERS FROM relative_strength_test.py
That test banded the STOCK's own 12-1 relative strength and came out null
(p=0.999). This bands the stock's SECTOR: the median 12-1 relative strength of
every universe member in that GICS sector, which is exactly what
features/sector_strength.py computes live and hands to the ranking prompt.
A stock can lag inside a leading sector, and that is a different claim.

BANDS, FIXED BEFORE ANY OUTCOME WAS LOOKED AT
    leading sector   sector median RS >= +5pp
    neutral          -5pp < median < +5pp
    lagging sector   sector median RS <= -5pp

POINT-IN-TIME
The median for a setup on date D uses daily closes strictly BEFORE D for every
member and for ^GSPC. Computed once per (sector, date) and cached.

WHAT IT CANNOT SAY
The store runs 2024-09 to 2026-09 -- two years, but FREEZE.md notes it holds
no down-trending session. A pass here is a lead for the 2018/2020/2022 data,
not a rule.

RESULT, 2026-10-04 (policy ba95546dbdbb, 1468 setups)
    train    leading -0.045R n=146   neutral -0.118R n=364   lagging -0.118R n=469
    holdout  leading +0.692R n=108   neutral +0.277R n=133   lagging +0.203R n=248
    train picked leading over lagging, gap +0.073R; holdout gap +0.489R, p=0.030
Held direction and cleared 0.05 -- the first sector-shaped idea here not to
flip. But the train gap is small, every holdout band is positive (the shape
that sank breadth), it is roughly the 25th idea tested, and leading sectors
were 17% of setups: as a filter it would drop ~83% of them. A lead.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
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
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import SESSION_ALIGNED
from scanner.screens.smc import scan

from relative_strength_test import momentum_12_1, permutation_p

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
BANDS = ("leading sector", "neutral", "lagging sector")
EDGE = 5.0


def band(median_rs: float) -> str:
    """Fixed before looking at a single outcome."""
    if median_rs >= EDGE:
        return "leading sector"
    if median_rs <= -EDGE:
        return "lagging sector"
    return "neutral"


def sectors(path: Path = Path("data/constituents.csv")) -> dict[str, str]:
    with path.open(encoding="utf-8") as handle:
        return {row["Symbol"].strip(): (row.get("GICS Sector") or "").strip()
                for row in csv.DictReader(handle) if row.get("Symbol")}


class SectorMedians:
    """Point-in-time sector median RS, one computation per (sector, date)."""

    def __init__(self, daily, sector_of, bench_dates, bench_closes):
        self._members = defaultdict(list)
        for symbol, series in daily.items():
            sector = sector_of.get(symbol)
            if sector:
                self._members[sector].append(
                    (list(series.dates), list(series.closes)))
        self._bench = (bench_dates, bench_closes)
        self._cache: dict[tuple[str, object], float | None] = {}

    def at(self, sector: str, when) -> float | None:
        key = (sector, when)
        if key not in self._cache:
            dates, closes = self._bench
            index = momentum_12_1(closes[:sum(1 for d in dates if d < when)])
            values = []
            for m_dates, m_closes in self._members.get(sector, []):
                stock = momentum_12_1(m_closes[:sum(1 for d in m_dates if d < when)])
                if stock is not None and index is not None:
                    values.append(stock - index)
            self._cache[key] = statistics.median(values) if len(values) >= 5 else None
        return self._cache[key]


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
        se = statistics.stdev(values) / len(values) ** 0.5 if len(values) > 1 else 0.0
        thin = "" if len(values) >= min_group else f"  {YELLOW}thin{RESET}"
        win = sum(r > 0 for r in values) / len(values) * 100
        tint = GREEN if mean > 0 else RED
        print(f"    {name:<16}{tint}{mean:>+7.3f}R{RESET}  se {se:>5.3f}  "
              f"n={len(values):<5} win {win:>4.0f}%{thin}")
        if len(values) >= min_group:
            usable.append(name)
    return usable


async def main(store: str, holdout: float, draws: int, min_group: int) -> int:
    path = Path(store)
    history.STORE_DIR = path
    sector_of = sectors()
    symbols = sorted({f.name.split("__")[0] for f in path.glob("*.json")})
    print(f"{DIM}fetching 5y daily for {len(symbols)} symbols + ^GSPC...{RESET}")
    async with httpx.AsyncClient(timeout=60) as client:
        benchmark = await fetch_bars("^GSPC", client, range_="5y")
        daily, _ = await fetch_many(tuple(symbols), client, range_="5y")
    medians = SectorMedians(daily, sector_of, list(benchmark.dates),
                            list(benchmark.closes))

    rows, unmapped = [], 0
    for symbol in symbols:
        sector = sector_of.get(symbol)
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if not sector:
            unmapped += 1
            continue
        if bars is None or len(bars) < 60:
            continue
        for setup in scan(bars, k=V.swing_k,
                          require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct,
                          min_reward_risk=V.min_reward_risk):
            i = setup.trigger_index
            if crosses_session(bars, i) or setup.entry_price - setup.stop_price <= 0:
                continue
            if bars.times[i].weekday() >= V.wind_down_from_weekday:
                continue
            median = medians.at(sector, bars.times[i].date())
            if median is None:
                continue
            outcome = resolve(bars, symbol=setup.symbol, entry_index=i,
                              entry_price=setup.entry_price,
                              stop_price=setup.stop_price,
                              target_price=setup.target_price,
                              max_bars=V.max_hold_bars)
            if outcome:
                rows.append((bars.times[i], band(median), outcome.r_multiple))

    rows.sort(key=lambda row: row[0])        # by time, so holdout is the future
    scored = [(b, r) for _, b, r in rows]
    n = len(scored)
    if n < 60:
        print(f"{YELLOW}only {n} scored{RESET}")
        return 0
    cut = int(n * (1 - holdout))
    print(f"\n{DIM}{n} setups scored (Mon-Thu, policy {V.fingerprint}); train {cut}, "
          f"holdout {n - cut}; {unmapped} store symbols had no GICS sector{RESET}")
    print(f"{DIM}train {rows[0][0]:%Y-%m-%d} .. {rows[cut - 1][0]:%Y-%m-%d}   "
          f"holdout {rows[cut][0]:%Y-%m-%d} .. {rows[-1][0]:%Y-%m-%d}{RESET}")
    mix = defaultdict(int)
    for b, _ in scored:
        mix[b] += 1
    print(f"{DIM}mix: " + ", ".join(f"{b} {mix[b]} ({mix[b] / n * 100:.0f}%)"
                                    for b in BANDS if mix[b]) + RESET)

    usable = show(scored[:cut], "TRAIN - choose here", min_group)
    show(scored[cut:], "HOLDOUT - same bands, unchanged", min_group)

    print(f"\n{BOLD}DOES IT SURVIVE?{RESET}")
    if len(usable) < 2:
        print(f"  {YELLOW}fewer than two bands reach n>={min_group}{RESET}")
        return 0
    train, hold = defaultdict(list), defaultdict(list)
    for b, r in scored[:cut]:
        train[b].append(r)
    for b, r in scored[cut:]:
        hold[b].append(r)
    best = max(usable, key=lambda b: statistics.fmean(train[b]))
    worst = min(usable, key=lambda b: statistics.fmean(train[b]))
    train_gap = statistics.fmean(train[best]) - statistics.fmean(train[worst])
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
        print(f"  {RED}SIGN FLIPPED -- the signature of noise{RESET}")
    elif p < 0.05:
        print(f"  {GREEN}holds direction and clears p<0.05 -- a lead for the "
              f"2018/2020/2022 data, not a rule{RESET}")
    else:
        print(f"  {DIM}same direction, not significant{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="data/intraday_alpaca")
    parser.add_argument("--holdout", type=float, default=0.333)
    parser.add_argument("--draws", type=int, default=20_000)
    parser.add_argument("--min-group", type=int, default=10)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.store, args.holdout, args.draws,
                                      args.min_group)))
