"""Is 30 minutes the right bar for this pattern?

    python scripts/timeframe_test.py
    python scripts/timeframe_test.py --store data/intraday

READ-ONLY research over an accumulated store.

WHY THIS IS ASKABLE NOW AND WAS NOT BEFORE
The Yahoo store holds 96 sessions. Aggregating those into 4-hour bars leaves
roughly 160 bars per symbol, which cannot carry a train/holdout split. The
Alpaca SIP store holds two years and 3.3M bars, so a coarser series still has
enough setups to test.

THE TWO CONFOUNDS THAT WOULD OTHERWISE DECIDE THE ANSWER

1. THE HOLD LIMIT IS COUNTED IN BARS. `max_hold_bars = 60` is 30 hours of
   trading on 30m bars — about 4.6 sessions. The same 60 bars on 240m bars is
   240 hours, roughly 37 sessions. Comparing those is comparing hold horizons,
   not timeframes, and the longer horizon would win or lose for reasons that
   have nothing to do with the bar size. So the limit is held constant in
   MINUTES here: 60 x 30 = 1800 minutes on every series.

2. THE SESSION DOES NOT DIVIDE EVENLY. A regular session is 390 minutes, which
   is 13 x 30 and is not a whole multiple of 60, 90, 120 or 240. The repo
   already documents this for 4H: "one full bar (09:30-13:30) plus a 2.5-hour
   stub". Buckets here are chunked WITHIN a session and never across one, so
   the last bar of a session is short rather than spanning the overnight gap.
   A short bar of real trades beats a long bar of invented ones — the same
   choice `resample` makes for missing hours.

WHAT IS NOT VARIED
`min_risk_pct`, `min_reward_risk` and `swing_k` stay at policy values. Retuning
them per timeframe would add free parameters and the comparison would stop
being about the bar.

TRIAL COUNT, DECLARED
Five timeframes, one test each. Five trials, to be added to the 50 already
declared when the deflated Sharpe is next computed. The eight `require_*`
context flags are deliberately NOT crossed with this: each was already tested
individually and each was null, and a 5 x 8 grid would add forty trials to a
deflated Sharpe that already sits at 0.82.
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

from scanner.backtest.setups import resolve
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED, IntradayBars
from scanner.screens.smc import scan

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
BASE = 30
#: The hold limit in MINUTES, so every timeframe gets the same wall clock.
HOLD_MINUTES = V.max_hold_bars * BASE
TIMEFRAMES = (30, 60, 90, 120, 240)


def coarsen(bars: IntradayBars, minutes: int) -> IntradayBars | None:
    """Fold 30m bars into `minutes` bars, never across a session boundary.

    Open is the first open in the bucket, close the last close, high and low
    the extremes, volume the sum — the only aggregation that keeps a bar
    honest. The final bucket of a session may be short.
    """
    if minutes == bars.interval_minutes:
        return bars
    step = minutes // bars.interval_minutes
    if step < 1 or minutes % bars.interval_minutes:
        return None
    by_day: dict[object, list[int]] = defaultdict(list)
    for i, moment in enumerate(bars.times):
        by_day[moment.date()].append(i)

    times, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    for day in sorted(by_day):
        indexes = by_day[day]
        for start in range(0, len(indexes), step):
            chunk = indexes[start:start + step]
            times.append(bars.times[chunk[0]])
            opens.append(bars.opens[chunk[0]])
            highs.append(max(bars.highs[i] for i in chunk))
            lows.append(min(bars.lows[i] for i in chunk))
            closes.append(bars.closes[chunk[-1]])
            volumes.append(sum(bars.volumes[i] for i in chunk))
    if len(times) < 60:
        return None
    return IntradayBars(
        symbol=bars.symbol, interval_minutes=minutes,
        alignment=bars.alignment,
        times=tuple(times), opens=tuple(opens), highs=tuple(highs),
        lows=tuple(lows), closes=tuple(closes), volumes=tuple(volumes),
    )


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


def collect(store: Path, minutes: int) -> list[tuple[float, float]]:
    """(position in series, R) for every resolved setup at this timeframe."""
    symbols = sorted({f.name.split("__")[0] for f in store.glob("*.json")})
    hold = max(1, HOLD_MINUTES // minutes)
    rows = []
    for symbol in symbols:
        base = history.accumulated(symbol, BASE, SESSION_ALIGNED)
        if base is None or len(base) < 60:
            continue
        bars = coarsen(base, minutes)
        if bars is None:
            continue
        for setup in scan(bars, k=V.swing_k,
                          require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct,
                          min_reward_risk=V.min_reward_risk):
            if crosses_session(bars, setup.trigger_index):
                continue
            outcome = resolve(
                bars, symbol=setup.symbol, entry_index=setup.trigger_index,
                entry_price=setup.entry_price, stop_price=setup.stop_price,
                target_price=setup.target_price, max_bars=hold,
            )
            if outcome:
                rows.append((setup.trigger_index / len(bars),
                             outcome.r_multiple))
    rows.sort(key=lambda row: row[0])
    return rows


def main(store: str, holdout: float, draws: int) -> int:
    path = Path(store)
    history.STORE_DIR = path
    if not path.exists():
        print(f"{RED}{path} does not exist{RESET}")
        return 2

    print(f"{DIM}hold limit fixed at {HOLD_MINUTES} minutes "
          f"({V.max_hold_bars} x {BASE}m) on every timeframe{RESET}")
    print(f"{DIM}risk {V.min_risk_pct}%, R:R {V.min_reward_risk}, k={V.swing_k} "
          f"unchanged throughout{RESET}\n")

    gathered: dict[int, list[tuple[float, float]]] = {}
    print(f"{BOLD}{'bar':>6}{'setups':>9}{'hold':>7}"
          f"{'ALL':>11}{'TRAIN':>11}{'HOLDOUT':>11}{'win':>8}{RESET}")
    for minutes in TIMEFRAMES:
        rows = collect(path, minutes)
        gathered[minutes] = rows
        if len(rows) < 30:
            print(f"{minutes:>5}m{len(rows):>9}   {YELLOW}too few to split"
                  f"{RESET}")
            continue
        rs = [r for _, r in rows]
        cut = int(len(rs) * (1 - holdout))
        train, hold_ = rs[:cut], rs[cut:]
        win = sum(1 for r in rs if r > 0) / len(rs) * 100
        tint = GREEN if statistics.fmean(rs) > 0 else RED
        mark = "  <- live" if minutes == V.interval_minutes else ""
        print(f"{minutes:>5}m{len(rows):>9}{max(1, HOLD_MINUTES // minutes):>6}b"
              f"{tint}{statistics.fmean(rs):>+11.3f}{RESET}"
              f"{statistics.fmean(train):>+11.3f}"
              f"{statistics.fmean(hold_):>+11.3f}{win:>7.1f}%{DIM}{mark}{RESET}")

    live = gathered.get(V.interval_minutes, [])
    if not live:
        return 0
    base_hold = [r for _, r in live][int(len(live) * (1 - holdout)):]
    print(f"\n{BOLD}AGAINST THE LIVE 30m, ON HOLDOUT ONLY{RESET}")
    for minutes in TIMEFRAMES:
        if minutes == V.interval_minutes or len(gathered[minutes]) < 30:
            continue
        rs = [r for _, r in gathered[minutes]]
        other = rs[int(len(rs) * (1 - holdout)):]
        if len(other) < 10:
            print(f"  {minutes:>4}m  {YELLOW}holdout too small "
                  f"({len(other)}){RESET}")
            continue
        gap = statistics.fmean(other) - statistics.fmean(base_hold)
        p = permutation_p(list(other), list(base_hold), draws)
        verdict = (f"{GREEN}better, p<0.05{RESET}" if gap > 0 and p < 0.05
                   else f"{RED}worse, p<0.05{RESET}" if gap < 0 and p < 0.05
                   else f"{DIM}not distinguishable{RESET}")
        print(f"  {minutes:>4}m  {gap:>+7.3f}R vs 30m   "
              f"n={len(other)}/{len(base_hold)}  p={p:.3f}   {verdict}")

    print(f"\n{DIM}Five trials. Add them to the declared count before quoting a "
          f"deflated Sharpe; it already sits at 0.82 against 50.{RESET}")
    print(f"{DIM}A timeframe that wins here has NOT been shown to work live: "
          f"the whole session-geometry stub, the freshness window and the 23x5 "
          f"change all interact with bar size.{RESET}")
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
