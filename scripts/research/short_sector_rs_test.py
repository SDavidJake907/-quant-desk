"""Was the stock beating its own sector over the five days before the sweep?

    python scripts/short_sector_rs_test.py

READ-ONLY research. Changes nothing live.

WHERE THIS CAME FROM
gemma4:12b, asked on 2026-10-04 for ideas unlike the recorded nulls: "filter
for setups where the stock is outperforming its GICS sector peers over a
rolling 5-day window -- the sweep is a liquidity grab by a buyer, not a
breakdown by a seller". Every relative-strength test here used 12-1
momentum against the S&P; this is short-horizon and against the SECTOR.

BANDS, FIXED BEFORE ANY OUTCOME WAS LOOKED AT
    beating sector   stock 5d return minus sector-median 5d return >= +2pp
    in line          between
    lagging sector   <= -2pp
Point-in-time: daily closes strictly before the trigger's date, for the stock
and every sector member (median over members with data, >= 5 required).

OUTCOMES WITH THE FRIDAY FLATTEN (flatten_gap_test.walk), so the numbers are
the strategy that runs, not resolve()'s weekend-holding one (WORKLIST §29).
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history
from scanner.features.bars import fetch_many
from scanner.features.intraday import SESSION_ALIGNED
from scanner.screens.smc import scan

from flatten_gap_test import walk
from relative_strength_test import permutation_p
from sector_leadership_test import sectors

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
BANDS = ("beating sector", "in line", "lagging sector")
EDGE, DAYS = 2.0, 5


def five_day(dates, closes, when) -> float | None:
    cut = sum(1 for d in dates if d < when)
    if cut < DAYS + 1:
        return None
    start, end = closes[cut - 1 - DAYS], closes[cut - 1]
    return (end / start - 1) * 100 if start > 0 else None


def band(gap: float) -> str:
    return "beating sector" if gap >= EDGE else "lagging sector" if gap <= -EDGE else "in line"


async def main(store: str, holdout: float, draws: int, min_group: int) -> int:
    path = Path(store)
    history.STORE_DIR = path
    sector_of = sectors()
    symbols = sorted({f.name.split("__")[0] for f in path.glob("*.json")})
    async with httpx.AsyncClient(timeout=60) as client:
        daily, _ = await fetch_many(tuple(symbols), client, range_="5y")
    members = defaultdict(list)
    for s, series in daily.items():
        if sector_of.get(s):
            members[sector_of[s]].append((list(series.dates), list(series.closes)))
    cache: dict = {}

    def sector_median(sector, when):
        key = (sector, when)
        if key not in cache:
            vals = [v for d, c in members[sector] if (v := five_day(d, c, when)) is not None]
            cache[key] = statistics.median(vals) if len(vals) >= 5 else None
        return cache[key]

    rows = []
    for symbol in symbols:
        sector, series = sector_of.get(symbol), daily.get(symbol)
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if not sector or series is None or bars is None or len(bars) < 60:
            continue
        dates, closes = list(series.dates), list(series.closes)
        for setup in scan(bars, k=V.swing_k, require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct, min_reward_risk=V.min_reward_risk):
            i = setup.trigger_index
            if crosses_session(bars, i) or setup.entry_price - setup.stop_price <= 0:
                continue
            if bars.times[i].weekday() >= V.wind_down_from_weekday:
                continue
            if i + V.max_hold_bars > len(bars) - 1:
                continue                      # unresolved at the end of the store
            when = bars.times[i].date()
            mine, peers = five_day(dates, closes, when), sector_median(sector, when)
            if mine is None or peers is None:
                continue
            rows.append((bars.times[i], band(mine - peers), walk(bars, setup, flatten=True)[0]))

    rows.sort(key=lambda row: row[0])
    n = len(rows)
    cut = int(n * (1 - holdout))
    print(f"\n{n} setups (Mon-Thu, policy {V.fingerprint}, Friday flatten applied); "
          f"train {cut} {rows[0][0]:%Y-%m-%d}..{rows[cut - 1][0]:%Y-%m-%d}, "
          f"holdout {n - cut} ..{rows[-1][0]:%Y-%m-%d}")
    split = {"train": rows[:cut], "holdout": rows[cut:]}
    groups = {k: {b: [r for _, bb, r in v if bb == b] for b in BANDS} for k, v in split.items()}
    for name in BANDS:
        cells = []
        for part in ("train", "holdout"):
            vals = groups[part][name]
            if vals:
                mean = statistics.fmean(vals)
                tint = GREEN if mean > 0 else RED
                cells.append(f"{part} {tint}{mean:+.3f}R{RESET} n={len(vals):<4}"
                             f" win {sum(v > 0 for v in vals) / len(vals) * 100:.0f}%")
        print(f"  {name:<16}" + "   ".join(cells))
    train = {b: v for b, v in groups["train"].items() if len(v) >= min_group}
    if len(train) < 2:
        print(f"{YELLOW}too thin to choose on train{RESET}")
        return 0
    best = max(train, key=lambda b: statistics.fmean(train[b]))
    worst = min(train, key=lambda b: statistics.fmean(train[b]))
    a, b = groups["holdout"][best], groups["holdout"][worst]
    train_gap = statistics.fmean(train[best]) - statistics.fmean(train[worst])
    if len(a) < min_group or len(b) < min_group:
        print(f"{YELLOW}holdout too thin ({len(a)} vs {len(b)}){RESET}")
        return 0
    gap = statistics.fmean(a) - statistics.fmean(b)
    p = permutation_p(list(a), list(b), draws)
    print(f"\n  train picked {best} over {worst} ({train_gap:+.3f}R); "
          f"holdout {gap:+.3f}R  p={p:.3f}")
    if (gap > 0) != (train_gap > 0):
        print(f"  {RED}SIGN FLIPPED{RESET}")
    elif p < 0.05:
        print(f"  {GREEN}holds direction, p<0.05 -- a lead{RESET}")
    else:
        print(f"  {DIM}same direction, not significant{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--store", default="data/intraday_alpaca")
    p.add_argument("--holdout", type=float, default=0.333)
    p.add_argument("--draws", type=int, default=20_000)
    p.add_argument("--min-group", type=int, default=20)
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.store, a.holdout, a.draws, a.min_group)))
