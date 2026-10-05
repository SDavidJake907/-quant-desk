"""Where should the entry limit sit? Fill rate against edge, two offset families.

    python scripts/limit_offset_test.py

READ-ONLY research. Changes nothing live. Uses every CPU core.

WHY
Live sends a buy limit AT the trigger close. Three of eighteen never filled
(META 09-29, LULU 10-01, SLB 10-05), and in all three the price TOUCHED the
limit -- META's low 15c under, LULU 4.5c, SLB half a cent -- without a fill.
A touch is not a fill: the shares at that price go to the orders queued ahead.
These setups bounce fast, so the exact signal price is rarely traded through.

FILL MODEL (conservative): the limit fills only if a later bar trades THROUGH
it -- low strictly below the limit -- within fresh_bars (2) bars, the window
before cancel_stale_entries removes it; the fill is min(bar open, limit).
From the fill bar: stop before target on a bar touching both, Friday flatten,
60-bar cap, 10bps in R. R is against the setup's ORIGINAL risk (close - stop),
the way live sizes it, so paying up shows as a smaller R.

OFFSETS, fixed before looking:
    % of price     0, 0.05, 0.10, 0.20, 0.30, 0.50
    share of risk  0.05R, 0.10R, 0.20R     (scales with each stop's distance)
Read TOTAL R: a higher limit fills more trades but pays more for each.
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
OFFSETS = [("pct", 0.0), ("pct", 0.0005), ("pct", 0.0010), ("pct", 0.0020), ("pct", 0.0030),
           ("pct", 0.0050), ("risk", 0.05), ("risk", 0.10), ("risk", 0.20)]


def _label(kind, x):
    return f"+{x * 100:.2f}% of price" if kind == "pct" else f"+{x:.2f}R of risk"


def _trade(bars, s, limit, window, last):
    close, stop, target = s.entry_price, s.stop_price, s.target_price
    risk = close - stop
    for j in range(s.trigger_index + 1, min(s.trigger_index + window, last) + 1):
        if bars.lows[j] < limit:                       # traded THROUGH, not touched
            fill = min(bars.opens[j], limit)
            for i in range(j, last + 1):
                o, h, lo = bars.opens[i], bars.highs[i], bars.lows[i]
                if i > j and (o <= stop or o >= target):
                    return (o - fill) / risk - COST * fill / risk
                if lo <= stop:
                    return (stop - fill) / risk - COST * fill / risk
                if h >= target:
                    return (target - fill) / risk - COST * fill / risk
                if bars.times[i].weekday() == 4 and (i == last or bars.times[i + 1].date() != bars.times[i].date()):
                    return (bars.closes[i] - fill) / risk - COST * fill / risk
            return (bars.closes[last] - fill) / risk - COST * fill / risk
    return None                                        # never filled


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
        if bars.times[i].weekday() >= V.wind_down_from_weekday or i + V.max_hold_bars + 2 > len(bars) - 1:
            continue
        lo = max(0, i - 20)
        dvol = statistics.fmean(bars.volumes[k] * bars.closes[k] for k in range(lo, i)) if i > lo else 0
        if bars.closes[i] < V.min_price or dvol < V.min_dollar_volume:
            continue
        last = min(i + V.max_hold_bars, len(bars) - 1)
        risk = s.entry_price - s.stop_price
        r = {}
        for kind, x in OFFSETS:
            limit = s.entry_price * (1 + x) if kind == "pct" else s.entry_price + x * risk
            r[(kind, x)] = _trade(bars, s, limit, V.fresh_bars, last)
        out.append({"at": bars.times[i], "r": r})
    return out


def main() -> int:
    symbols = sorted({f.name.split("__")[0] for f in Path(STORE).glob("*.json")})
    rows = []
    with ProcessPoolExecutor() as pool:
        for chunk in pool.map(_work, symbols, chunksize=8):
            rows.extend(chunk)
    rows.sort(key=lambda r: r["at"])
    cut = len(rows) * 2 // 3
    print(f"\n{len(rows)} gated setups; fill = traded THROUGH the limit within 2 bars; "
          f"train to {rows[cut - 1]['at']:%Y-%m-%d}")
    for name, part in (("TRAIN", rows[:cut]), ("HOLDOUT", rows[cut:])):
        print(f"\n  {name}  ({len(part)} setups)")
        print(f"    {'limit':<20}{'filled':>8}{'mean R':>9}{'total R':>9}{'win':>6}")
        for key in OFFSETS:
            got = [r["r"][key] for r in part if r["r"][key] is not None]
            if not got:
                continue
            print(f"    {_label(*key):<20}{len(got) / len(part) * 100:>7.0f}%"
                  f"{statistics.fmean(got):>+9.3f}{sum(got):>+9.1f}"
                  f"{sum(v > 0 for v in got) / len(got) * 100:>5.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
