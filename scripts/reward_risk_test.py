"""Can these setups carry a 1:5 reward:risk? Two readings, same 2y store.

    python scripts/reward_risk_test.py

READ-ONLY research. Changes nothing live.

1. BY PLANNED REWARD:RISK. The target is the range high, so every setup comes
   with its own R:R. Bands 1.5-2 / 2-3 / 3-5 / 5+: how often does the target
   actually hit, and what does the band earn? Break-even win rate for a 1:N
   trade is 1/(N+1): 1:2 needs 33%, 1:3 25%, 1:5 16.7%.
2. FORCED 5R TARGET. Every setup with its target moved to entry + 5 x risk,
   same stop, same Friday flatten. Does stretching the target pay?
Gated as live (price, $20M/bar), Mon-Thu, 10bps costs in R.
"""

from __future__ import annotations

import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STORE = "data/intraday_alpaca"
COST = 0.0010
BANDS = ((1.5, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 99.0))


def _walk(bars, i, entry, stop, target, last):
    for j in range(i + 1, last + 1):
        o, h, l = bars.opens[j], bars.highs[j], bars.lows[j]
        if o <= stop or o >= target:
            return o, "target" if o >= target else "stop"
        if l <= stop:
            return stop, "stop"
        if h >= target:
            return target, "target"
        if bars.times[j].weekday() == 4 and (j == last or bars.times[j + 1].date() != bars.times[j].date()):
            return bars.closes[j], "flattened"
    return bars.closes[last], "cap"


def _work(symbol):
    from scanner.execution.policy import TradingPolicy
    from scanner.execution.session import crosses_session
    from scanner.features import history
    from scanner.features.intraday import SESSION_ALIGNED
    from scanner.screens.smc import scan
    V = TradingPolicy()
    history.STORE_DIR = Path(STORE)
    bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
    if bars is None or len(bars) < 60:
        return []
    out = []
    for s in scan(bars, k=V.swing_k, require_value_sweep=V.require_value_sweep,
                  min_risk_pct=V.min_risk_pct, min_reward_risk=V.min_reward_risk):
        i = s.trigger_index
        if crosses_session(bars, i) or s.entry_price - s.stop_price <= 0:
            continue
        if bars.times[i].weekday() >= V.wind_down_from_weekday or i + V.max_hold_bars > len(bars) - 1:
            continue
        lo = max(0, i - 20)
        dvol = statistics.fmean(bars.volumes[k] * bars.closes[k] for k in range(lo, i)) if i > lo else 0
        if bars.closes[i] < V.min_price or dvol < V.min_dollar_volume:
            continue
        entry, stop = s.entry_price, s.stop_price
        risk = entry - stop
        cost_r = COST * entry / risk
        last = min(i + V.max_hold_bars, len(bars) - 1)
        p1, why1 = _walk(bars, i, entry, stop, s.target_price, last)
        p5, why5 = _walk(bars, i, entry, stop, entry + 5 * risk, last)
        out.append({"at": bars.times[i], "rr": (s.target_price - entry) / risk,
                    "r": (p1 - entry) / risk - cost_r, "hit": why1 == "target",
                    "r5": (p5 - entry) / risk - cost_r, "hit5": why5 == "target"})
    return out


def line(label, rows, rkey, hkey, need=None):
    if not rows:
        print(f"  {label:<16} n=0")
        return
    r = [x[rkey] for x in rows]
    hit = sum(x[hkey] for x in rows) / len(rows) * 100
    mean = statistics.fmean(r)
    tint = "\033[32m" if mean > 0 else "\033[31m"
    extra = f"   needs >{need:.1f}%" if need else ""
    print(f"  {label:<16} n={len(rows):<5} target hit {hit:>5.1f}%{extra:<15} "
          f"mean {tint}{mean:+.3f}R\033[0m   total {sum(r):+.0f}R")


def main() -> int:
    symbols = sorted({f.name.split("__")[0] for f in Path(STORE).glob("*.json")})
    rows = []
    with ProcessPoolExecutor() as pool:
        for chunk in pool.map(_work, symbols, chunksize=8):
            rows.extend(chunk)
    rows.sort(key=lambda r: r["at"])
    half = len(rows) // 2
    for label, part in (("ALL", rows), ("FIRST HALF", rows[:half]), ("SECOND HALF", rows[half:])):
        print(f"\n{label}  ({len(part)} gated setups, 10bps in R, Friday flatten)")
        print(" by the setup's own planned reward:risk (target = range high):")
        for lo, hi in BANDS:
            band = [x for x in part if lo <= x["rr"] < hi]
            mid = (lo + min(hi, 6)) / 2
            line(f"1:{lo:g}-{hi:g}" if hi < 99 else f"1:{lo:g}+", band, "r", "hit", 100 / (mid + 1))
        print(" every setup with the target forced to 5R:")
        line("1:5 forced", part, "r5", "hit5", 100 / 6)
        line("own target", part, "r", "hit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
