"""Does the backtest trade what the live limit order would actually get? And: time of day.

    python scripts/live_fill_test.py

READ-ONLY research. Changes nothing live.

THE FILL THE BACKTEST ASSUMES, AND THE ONE LIVE GETS
Every backtest here enters at the trigger bar's close. Live sends a LIMIT buy
at that price (bracket_payload, gtc) a few minutes after the bar closes, and
cancel_stale_entries kills it after fresh_bars x 30 = 60 minutes unfilled. So:
    - a setup that runs straight up never trades back to the limit: NO TRADE
      live, but a full trade in every backtest -- likely a winner
    - a setup that falls through the limit fills BELOW it (at the bar's open
      when it gaps under): the APP and PSX trades, filled ~1.1% under signal,
      0.5% from the stop, stopped within three bars
Modelled here: the limit fills on the first of the next two bars whose low
reaches it, at min(open, limit). From the fill bar on, stop is checked before
target (the conservative reading of a bar that touches both). R is against the
INTENDED risk, as live sizes it. Friday flatten applied.

(b) RULES, FIXED BEFORE LOOKING: fill gap = (limit - fill) / limit.
    "deep fill" gap >= 0.5%; candidate rule: skip deep fills.
(a) TIME OF DAY: slot of the trigger bar, open < 11:00 <= mid < 14:00 <= close
    (ET). CONTAMINATED: this split was noticed in trade_anatomy.py on the SAME
    store, so its train/holdout is not independent of the idea -- a pass here
    is a hypothesis for fresh data, not a finding.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED
from scanner.screens.smc import scan

from flatten_gap_test import walk
from relative_strength_test import permutation_p

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
WINDOW = V.fresh_bars            # bars the limit may rest before it is cancelled
DEEP = 0.005


def live_trade(bars, setup):
    """(R vs intended risk, fill gap) or (None, None) if the limit never fills."""
    limit, stop, target = setup.entry_price, setup.stop_price, setup.target_price
    risk = limit - stop
    first = setup.trigger_index
    last = min(first + V.max_hold_bars, len(bars) - 1)
    fill_bar = fill = None
    for j in range(first + 1, min(first + WINDOW, last) + 1):
        if bars.lows[j] <= limit:
            fill_bar, fill = j, min(bars.opens[j], limit)
            break
    if fill_bar is None:
        return None, None
    for i in range(fill_bar, last + 1):
        o, h, lo = bars.opens[i], bars.highs[i], bars.lows[i]
        if i > fill_bar and o <= stop:
            return (o - fill) / risk, (limit - fill) / limit
        if i > fill_bar and o >= target:
            return (o - fill) / risk, (limit - fill) / limit
        if lo <= stop:
            return (min(stop, o if i > fill_bar else stop) - fill) / risk, (limit - fill) / limit
        if h >= target:
            return (target - fill) / risk, (limit - fill) / limit
        if bars.times[i].weekday() == 4 and (i == last or bars.times[i + 1].date() != bars.times[i].date()):
            return (bars.closes[i] - fill) / risk, (limit - fill) / limit
    return (bars.closes[last] - fill) / risk, (limit - fill) / limit


def slot_of(t) -> str:
    return "open" if t.hour < 11 else "close" if t.hour >= 14 else "mid"


def stats(vals):
    if not vals:
        return "n=0"
    m = statistics.fmean(vals)
    return (f"{(GREEN if m > 0 else RED)}{m:+.3f}R{RESET} n={len(vals):<4} "
            f"win {sum(v > 0 for v in vals) / len(vals) * 100:.0f}%")


def main(store: str, holdout: float, draws: int) -> int:
    path = Path(store)
    history.STORE_DIR = path
    rows = []                         # (time, slot, backtest R, live R or None, gap)
    for symbol in sorted({f.name.split("__")[0] for f in path.glob("*.json")}):
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        for s in scan(bars, k=V.swing_k, require_value_sweep=V.require_value_sweep,
                      min_risk_pct=V.min_risk_pct, min_reward_risk=V.min_reward_risk):
            i = s.trigger_index
            if crosses_session(bars, i) or s.entry_price - s.stop_price <= 0:
                continue
            if bars.times[i].weekday() >= V.wind_down_from_weekday or i + V.max_hold_bars > len(bars) - 1:
                continue
            r_live, gap = live_trade(bars, s)
            rows.append((bars.times[i], slot_of(bars.times[i]),
                         walk(bars, s, flatten=True)[0], r_live, gap))
    rows.sort(key=lambda r: r[0])
    cut = int(len(rows) * (1 - holdout))
    parts = {"train": rows[:cut], "holdout": rows[cut:]}
    print(f"\n{len(rows)} setups, Friday flatten; train to {rows[cut - 1][0]:%Y-%m-%d}, "
          f"holdout {len(rows) - cut}; limit rests {WINDOW} bars")

    print(f"\n{BOLD}(b) BACKTEST FILL vs LIVE LIMIT FILL{RESET}")
    for name, rs in parts.items():
        missed = [r for r in rs if r[3] is None]
        filled = [r for r in rs if r[3] is not None]
        print(f"  {name}")
        print(f"    backtest, every setup at the close   {stats([r[2] for r in rs])}")
        print(f"    live limit, filled trades            {stats([r[3] for r in filled])}")
        print(f"    never filled ({len(missed) / len(rs) * 100:.0f}%), what the backtest "
              f"credited them  {stats([r[2] for r in missed])}")
        deep = [r[3] for r in filled if r[4] >= DEEP]
        normal = [r[3] for r in filled if r[4] < DEEP]
        print(f"    deep fill (>= {DEEP * 100:.1f}% under)             {stats(deep)}")
        print(f"    normal fill                          {stats(normal)}")
        print(f"    rule 'skip deep fills'               {stats(normal)}  vs all filled "
              f"{statistics.fmean([r[3] for r in filled]):+.3f}R")
    tr = [r for r in parts["train"] if r[3] is not None]
    ho = [r for r in parts["holdout"] if r[3] is not None]
    t_gap = (statistics.fmean([r[3] for r in tr if r[4] < DEEP])
             - statistics.fmean([r[3] for r in tr if r[4] >= DEEP]))
    h_norm = [r[3] for r in ho if r[4] < DEEP]
    h_deep = [r[3] for r in ho if r[4] >= DEEP]
    h_gap = statistics.fmean(h_norm) - statistics.fmean(h_deep)
    print(f"  normal minus deep: train {t_gap:+.3f}R, holdout {h_gap:+.3f}R, "
          f"p={permutation_p(h_norm, h_deep, draws):.3f}"
          + ("  SIGN FLIPPED" if (t_gap > 0) != (h_gap > 0) else ""))

    print(f"\n{BOLD}(a) TIME OF DAY{RESET}  {YELLOW}(contaminated -- see docstring){RESET}")
    for key, idx in (("backtest R", 2), ("live-fill R", 3)):
        print(f"  {key}")
        for slot in ("open", "mid", "close"):
            cells = [stats([r[idx] for r in parts[p] if r[1] == slot and r[idx] is not None])
                     for p in ("train", "holdout")]
            print(f"    {slot:<6} train {cells[0]}    holdout {cells[1]}")
    def vals(p, slot, idx=3):
        return [r[idx] for r in parts[p] if r[1] == slot and r[idx] is not None]
    t_gap = statistics.fmean(vals("train", "mid")) - statistics.fmean(vals("train", "open"))
    a, b = vals("holdout", "mid"), vals("holdout", "open")
    h_gap = statistics.fmean(a) - statistics.fmean(b)
    print(f"  mid minus open (live fills): train {t_gap:+.3f}R, holdout {h_gap:+.3f}R, "
          f"p={permutation_p(a, b, draws):.3f}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--store", default="data/intraday_alpaca")
    p.add_argument("--holdout", type=float, default=0.333)
    p.add_argument("--draws", type=int, default=20_000)
    a = p.parse_args()
    raise SystemExit(main(a.store, a.holdout, a.draws))
