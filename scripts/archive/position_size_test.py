"""What does raising the position-size cap do to the ACCOUNT? Two years, as it runs.

    python scripts/position_size_test.py

READ-ONLY research. Changes nothing live.

WHY THE CAP, NOT risk_pct, IS THE REAL DIAL
size_position takes the smaller of (equity x risk_pct / stop distance) and
(equity x max_position_pct / price). At 1% risk the first only wins when the
stop is MORE than 5% away (1/0.20); min_risk_pct is 1.5%, so on nearly every
trade the 20% cap binds and actual risk is 20% x stop distance -- 0.4% on a 2%
stop. Raising the cap to 30% raises real risk ~1.5x on those trades. Per-trade
R cannot show this; only the account can.

WHAT IS SIMULATED
Every Mon-Thu setup in the 2y Alpaca store that passes the live price, $20M
volume, stop and reward:risk gates, in time order. Entry at the trigger bar's
close, exit by the bracket with the FRIDAY FLATTEN (WORKLIST §29), max 5 open,
equity compounding. Cash account: a setup is skipped when deployed capital
leaves too little, as size_position skips it live. One symbol at a time.
Costs: 10bps round trip on the position value.

Configurations: cap 20% (live), 25%, 30% cash, and 30% with 2x buying power.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.execution.policy import TradingPolicy
from scanner.execution.sizing import size_position

from gemma_graded import collect, gates_for

V = TradingPolicy()
START = 100_000.0
COST = 0.0010
CONFIGS = (("cap 20% (live)", 20.0, 1.0), ("cap 25%", 25.0, 1.0),
           ("cap 30%", 30.0, 1.0), ("cap 30%, 2x margin", 30.0, 2.0))


def exit_of(bars, setup) -> tuple[float, int]:
    """(exit price, exit index) under the bracket with the Friday flatten."""
    stop, target = setup.stop_price, setup.target_price
    first = setup.trigger_index
    last = min(first + V.max_hold_bars, len(bars) - 1)
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


def simulate(trades, cap: float, margin: float) -> dict:
    equity, peak, max_dd = START, START, 0.0
    open_: list[dict] = []
    taken = skipped_cash = skipped_slot = 0
    risks, curve = [], []
    for t in trades:
        for pos in sorted([p for p in open_ if p["exit_at"] <= t["at"]], key=lambda p: p["exit_at"]):
            equity += pos["pnl"]
            open_.remove(pos)
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
            curve.append((pos["exit_at"], equity))
        if any(p["symbol"] == t["symbol"] for p in open_):
            continue
        if len(open_) >= V.max_positions:
            skipped_slot += 1
            continue
        deployed = sum(p["value"] for p in open_)
        sizing = size_position(equity, t["entry"], t["stop"], risk_pct=V.risk_pct,
                               max_position_pct=cap,
                               buying_power=equity * margin - deployed)
        if sizing.shares < 1:
            skipped_cash += 1
            continue
        value = sizing.shares * t["entry"]
        pnl = sizing.shares * (t["exit"] - t["entry"]) - value * COST
        risks.append(sizing.risk_dollars / equity * 100)
        open_.append({"symbol": t["symbol"], "exit_at": t["exit_at"], "value": value, "pnl": pnl})
        taken += 1
    for pos in sorted(open_, key=lambda p: p["exit_at"]):
        equity += pos["pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    total = equity / START - 1
    return {"return": total * 100, "dd": max_dd * 100,
            "ratio": total / max_dd if max_dd else float("inf"),
            "taken": taken, "cash": skipped_cash, "slot": skipped_slot,
            "risk": statistics.fmean(risks) if risks else 0.0}


def main(store: str, since: str) -> int:
    rows = collect(Path(store), date.fromisoformat(since))
    trades = []
    for when, bars, setup, _ in rows:
        past = bars.upto(setup.trigger_index)
        if not all(ok for _, ok, _ in gates_for(past, setup, None)):
            continue
        if setup.trigger_index + V.max_hold_bars > len(bars) - 1:
            continue
        price, idx = exit_of(bars, setup)
        trades.append({"symbol": bars.symbol, "at": when, "exit_at": bars.times[idx],
                       "entry": setup.entry_price, "stop": setup.stop_price, "exit": price})
    trades.sort(key=lambda t: t["at"])
    print(f"\n{len(trades)} gated setups {trades[0]['at']:%Y-%m-%d}..{trades[-1]['at']:%Y-%m-%d}, "
          f"max {V.max_positions} open, risk_pct {V.risk_pct}%, 10bps costs, Friday flatten")
    half = len(trades) // 2
    for label, part in (("ALL", trades), ("FIRST HALF", trades[:half]),
                        ("SECOND HALF", trades[half:])):
        print(f"\n  {label}")
        print(f"    {'config':<20}{'return':>9}{'max DD':>9}{'ret/DD':>8}{'risk/trade':>12}"
              f"{'taken':>7}{'no cash':>9}{'no slot':>9}")
        for name, cap, margin in CONFIGS:
            r = simulate(part, cap, margin)
            print(f"    {name:<20}{r['return']:>+8.1f}%{r['dd']:>8.1f}%{r['ratio']:>8.2f}"
                  f"{r['risk']:>11.2f}%{r['taken']:>7}{r['cash']:>9}{r['slot']:>9}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--store", default="data/intraday_alpaca")
    p.add_argument("--since", default="2024-01-01")
    a = p.parse_args()
    raise SystemExit(main(a.store, a.since))
