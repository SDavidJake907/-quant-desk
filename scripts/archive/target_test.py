"""Would a closer take-profit -- fixed, or sized to each stock -- turn small wins
into an edge?

    python scripts/target_test.py                                 # unseen 2018 store
    python scripts/target_test.py --store data/intraday_alpaca \\
        --since 2024-09-01 --until 2026-12-31                     # the 2y store

READ-ONLY research. Changes nothing live. Same setups, stop, gates, Friday
flatten, max hold and 10bps as gate1_test.py; ONLY the target moves.

PRIOR EVIDENCE (WORKLIST §24, §27): a 1.5R cap ("quick") earned +0.121R vs
+0.216R for the natural target; setups whose natural target is close
(1.5-2R) were the worst band in both halves. Neither tried targets under 1.5R
or targets sized per stock, and neither ran on the 2018 store.

THE ARITHMETIC: a target of k R needs a win rate above 1/(1+k) plus costs --
0.5R needs ~67%, 1R ~50%. A setup that goes straight to its stop loses -1R
whatever the target, so a closer target can only help if many trades reach
+kR and then turn round.

TARGETS, FIXED 2026-10-05 BEFORE THIS SCRIPT READ ANY DATA
    own         the live target (range high) -- the baseline
    0.5R 0.75R 1.0R 1.5R   entry + k x risk, capped at the own target
    0.5 ATRd    entry + half the stock's daily ATR (14 days, before the date),
                capped at the own target -- "what it is per stock"
    1.0 ATRd    entry + one daily ATR, capped at the own target
Judged on the unseen store: mean R, win rate against break-even, and the
5-slot account. Every arm uses the same setups, so differences are paired.
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
from gate1_test import COST, PERIODS, STORE, _atr_pct, _daily, account  # noqa: E402

ARMS = ("own", "0.5R", "0.75R", "1.0R", "1.5R", "0.5 ATRd", "1.0 ATRd")


def walk(bars, first: int, last: int, stop: float, target: float) -> tuple[float, int]:
    """(exit price, index) under the bracket with the Friday flatten -- exit_of's rules."""
    for i in range(first + 1, last + 1):
        o, h, lo = bars.opens[i], bars.highs[i], bars.lows[i]
        if o <= stop or o >= target:
            return o, i
        if lo <= stop:
            return stop, i
        if h >= target:
            return target, i
        if bars.times[i].weekday() == 4:
            nxt = i + 1
            if nxt > last or bars.times[nxt].date() != bars.times[i].date():
                return bars.closes[i], i
    return bars.closes[last], last


def targets(entry: float, risk: float, own: float, atrd: float | None) -> dict[str, float | None]:
    out: dict[str, float | None] = {"own": own}
    for k in (0.5, 0.75, 1.0, 1.5):
        out[f"{k}R"] = min(own, entry + k * risk)
    for k in (0.5, 1.0):
        out[f"{k} ATRd"] = min(own, entry * (1 + k * atrd / 100)) if atrd else None
    return out


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
    daily = _daily(bars)
    rows = []
    for setup in scan(bars, k=v.swing_k, require_value_sweep=v.require_value_sweep,
                      min_risk_pct=v.min_risk_pct, min_reward_risk=v.min_reward_risk):
        i = setup.trigger_index
        when = bars.times[i]
        entry, stop = setup.entry_price, setup.stop_price
        risk = entry - stop
        if not since <= when.date() <= until or risk <= 0 or crosses_session(bars, i):
            continue
        if when.weekday() >= v.wind_down_from_weekday or i + v.max_hold_bars > len(bars) - 1:
            continue
        if not all(ok for _, ok, _ in gates_for(bars.upto(i), setup, None)):
            continue
        last = i + v.max_hold_bars
        row = {"symbol": symbol, "at": when, "entry": entry, "stop": stop}
        for arm, target in targets(entry, risk, setup.target_price,
                                   _atr_pct(daily, when.date())).items():
            if target is None:
                continue
            price, idx = walk(bars, i, last, stop, target)
            row[arm] = {"exit": price, "exit_at": bars.times[idx],
                        "r": (price - entry) / risk - COST * entry / risk}
        rows.append(row)
    return rows


def main(store: str, since: date, until: date, workers: int) -> int:
    symbols = sorted({f.name.split("__")[0] for f in Path(store).glob("*.json")})
    with ProcessPoolExecutor(workers) as pool:
        rows = [r for chunk in pool.map(_work, [(s, store, since, until) for s in symbols])
                for r in chunk]
    rows.sort(key=lambda r: r["at"])
    rows = [r for r in rows if all(arm in r for arm in ARMS)]   # same setups in every arm
    print(f"{len(rows)} gated setups {rows[0]['at']:%Y-%m-%d}..{rows[-1]['at']:%Y-%m-%d}, "
          f"every arm on the same setups")
    print(f"\n   {'target':<10}{'mean R':>9}{'win':>6}{'needs':>7}{'account':>10}{'DD':>7}   per period (mean R)")
    for arm in ARMS:
        trades = [{"symbol": r["symbol"], "at": r["at"], "entry": r["entry"], "stop": r["stop"],
                   **r[arm]} for r in rows]
        rs = [t["r"] for t in trades]
        wins = [t for t in trades if t["r"] > 0]
        avg_win = statistics.fmean(t["r"] for t in wins) if wins else 0.0
        needs = 1 / (1 + avg_win) * 100 if avg_win else 100.0
        acct = account(trades)
        periods = "  ".join(f"{label} {statistics.fmean(t['r'] for t in trades if lo <= t['at'].date() <= hi):+.2f}"
                            for label, lo, hi, _ in PERIODS
                            if any(lo <= t["at"].date() <= hi for t in trades))
        print(f"   {arm:<10}{statistics.fmean(rs):>+8.3f}R{len(wins) / len(rs) * 100:>5.0f}%"
              f"{needs:>6.0f}%{acct['return']:>+9.1f}%{acct['dd']:>6.1f}%   {periods}")
    print("\n   'needs' = break-even win rate given that arm's average winner, before the "
          "losers' size moves")
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
