"""Scale out at 3-4 take-profit rungs, with and without moving the stop to
breakeven after the first rung fills.

    python scripts/ladder_test.py                                 # unseen 2018 store
    python scripts/ladder_test.py --store data/intraday_alpaca \\
        --since 2024-09-01 --until 2026-12-31                     # the 2y store

READ-ONLY research. Changes nothing live. Same setups, stop, gates, Friday
flatten and max hold as gate1_test.py; only the exit changes.

WHY THE STOP MOVE IS THE QUESTION
With a fixed stop each slice is an independent trade with its own target, so a
ladder is exactly the weighted average of single-target arms (target_test.py)
and cannot beat the best of them. Moving the stop to entry after rung 1 is
what makes a ladder different: the rest can no longer lose, but a trade that
dips back to entry before running is now closed flat.
Prior: the single "breakeven" plan (stop to entry at +1R, one target) earned
+0.085R vs +0.216R for the plain bracket (WORKLIST §27, 307 setups).

LADDERS, FIXED 2026-10-05 BEFORE THIS SCRIPT READ ANY DATA
(rungs above the own target are capped at it; slices are equal)
    plain        one exit at the own target (the live bracket) -- baseline
    A  fixed     1/3 @ 0.5R, 1/3 @ 1R, 1/3 @ own        stop never moves
    B  be        1/3 @ 0.5R, 1/3 @ 1R, 1/3 @ own        stop -> entry after rung 1
    C  be 4      1/4 @ 0.5R, 1R, 1.5R, own              stop -> entry after rung 1
    D  be wide   1/3 @ 1R, 2R, own                      stop -> entry after rung 1
Rules: a bar's open through the stop or a rung fills there; a bar that touches
both the stop and a rung is taken as the STOP (conservative); a moved stop
applies from the next bar. Costs: 10bps round trip on the position, plus
2bps for every exit leg beyond the first (each crosses the spread).
"""

from __future__ import annotations

import argparse
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, "scripts")
from gate1_test import COST, PERIODS, STORE, account  # noqa: E402

LEG_COST = 0.0002
LADDERS = {
    "plain": ((1.0,), (None,), False),
    "A fixed": ((1 / 3,) * 3, (0.5, 1.0, None), False),
    "B be": ((1 / 3,) * 3, (0.5, 1.0, None), True),
    "C be 4": ((0.25,) * 4, (0.5, 1.0, 1.5, None), True),
    "D be wide": ((1 / 3,) * 3, (1.0, 2.0, None), True),
}


def ladder_walk(bars, first, last, entry, stop, legs, to_breakeven):
    """legs = [(fraction, target)] ascending. Returns (R before costs, exit legs, last index)."""
    open_legs = sorted(legs, key=lambda leg: leg[1])
    pnl, exits, idx = 0.0, 0, first
    for i in range(first + 1, last + 1):
        idx = i
        o, h, lo = bars.opens[i], bars.highs[i], bars.lows[i]
        if o <= stop:
            return pnl + sum(f for f, _ in open_legs) * (o - entry), exits + 1, i
        filled_now = [leg for leg in open_legs if leg[1] <= o]
        for f, _ in filled_now:
            pnl += f * (o - entry)
        if filled_now:
            exits += 1
        open_legs = [leg for leg in open_legs if leg[1] > o]
        if open_legs and lo <= stop:
            return pnl + sum(f for f, _ in open_legs) * (stop - entry), exits + 1, i
        hit = [leg for leg in open_legs if leg[1] <= h]
        for f, t in hit:
            pnl += f * (t - entry)
        exits += len(hit)
        open_legs = [leg for leg in open_legs if leg[1] > h]
        if (filled_now or hit) and to_breakeven:
            stop = max(stop, entry)
        if not open_legs:
            return pnl, exits, i
        if bars.times[i].weekday() == 4:
            nxt = i + 1
            if nxt > last or bars.times[nxt].date() != bars.times[i].date():
                return pnl + sum(f for f, _ in open_legs) * (bars.closes[i] - entry), exits + 1, i
    return pnl + sum(f for f, _ in open_legs) * (bars.closes[idx] - entry), exits + 1, idx


def _work(args):
    symbol, store, since, until = args
    sys.path.insert(0, "scripts")
    from scanner.execution.policy import TradingPolicy
    from scanner.execution.session import crosses_session
    from scanner.features import history
    from scanner.features.intraday import SESSION_ALIGNED
    from scanner.screens.smc import scan
    from gemma_graded import gates_for

    v = TradingPolicy()
    history.STORE_DIR = Path(store)
    bars = history.accumulated(symbol, v.interval_minutes, SESSION_ALIGNED)
    if bars is None or len(bars) < 60:
        return []
    rows = []
    for setup in scan(bars, k=v.swing_k, require_value_sweep=v.require_value_sweep,
                      min_risk_pct=v.min_risk_pct, min_reward_risk=v.min_reward_risk):
        i = setup.trigger_index
        when = bars.times[i]
        entry, stop, own = setup.entry_price, setup.stop_price, setup.target_price
        risk = entry - stop
        if not since <= when.date() <= until or risk <= 0 or crosses_session(bars, i):
            continue
        if when.weekday() >= v.wind_down_from_weekday or i + v.max_hold_bars > len(bars) - 1:
            continue
        if not all(ok for _, ok, _ in gates_for(bars.upto(i), setup, None)):
            continue
        row = {"symbol": symbol, "at": when, "entry": entry, "stop": stop}
        for name, (fracs, rungs, be) in LADDERS.items():
            legs = [(f, own if k is None else min(own, entry + k * risk)) for f, k in zip(fracs, rungs)]
            raw, exits, idx = ladder_walk(bars, i, i + v.max_hold_bars, entry, stop, legs, be)
            cost = (COST + LEG_COST * max(0, exits - 1)) * entry
            r = (raw - cost) / risk
            # exit price equivalent, so the 5-slot account books the same dollars
            row[name] = {"r": r, "exit": entry + raw - cost + COST * entry,
                         "exit_at": bars.times[idx], "legs": exits}
        rows.append(row)
    return rows


def main(store: str, since: date, until: date, workers: int) -> int:
    symbols = sorted({f.name.split("__")[0] for f in Path(store).glob("*.json")})
    with ProcessPoolExecutor(workers) as pool:
        rows = [r for chunk in pool.map(_work, [(s, store, since, until) for s in symbols])
                for r in chunk]
    rows.sort(key=lambda r: r["at"])
    print(f"{len(rows)} gated setups {rows[0]['at']:%Y-%m-%d}..{rows[-1]['at']:%Y-%m-%d}, "
          f"every ladder on the same setups")
    print(f"\n   {'exit':<11}{'mean R':>9}{'win':>6}{'flat':>6}{'legs':>6}{'account':>10}{'DD':>7}"
          f"   per period (mean R)")
    for name in LADDERS:
        trades = [{"symbol": r["symbol"], "at": r["at"], "entry": r["entry"], "stop": r["stop"],
                   **r[name]} for r in rows]
        rs = [t["r"] for t in trades]
        acct = account(trades)
        flat = sum(abs(r) < 0.1 for r in rs) / len(rs) * 100
        periods = "  ".join(
            f"{label} {statistics.fmean(t['r'] for t in trades if lo <= t['at'].date() <= hi):+.2f}"
            for label, lo, hi, _ in PERIODS if any(lo <= t["at"].date() <= hi for t in trades))
        print(f"   {name:<11}{statistics.fmean(rs):>+8.3f}R{sum(r > 0 for r in rs) / len(rs) * 100:>5.0f}%"
              f"{flat:>5.0f}%{statistics.fmean(t['legs'] for t in trades):>6.1f}"
              f"{acct['return']:>+9.1f}%{acct['dd']:>6.1f}%   {periods}")
    print("\n   flat = trades closed within 0.1R of entry (stopped at breakeven)")
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
