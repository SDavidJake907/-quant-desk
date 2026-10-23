"""Do the classic indicator layers sort the sweep setups? Each alone, then stacked.

    python scripts/indicator_layers_test.py

READ-ONLY research over an accumulated store. Changes nothing live.

WHERE THIS CAME FROM
A layered reading of seven popular indicators, brought in 2026-10-04: build
the decision from structure first, then trend (200/50/21/9 EMA), momentum
(RSI, MACD), volatility and volume, and act on the COMBINATION of evidence,
not any one line. The structure layer is the screen itself -- every setup here
is a sweep of a swing low and a break, stopped at the higher low. This asks
whether the other layers, measured at the trigger bar, add anything.

Some of this was tested before (WORKLIST §12, "7 of 9 context gates worse")
on ~190 setups. The 2024-09..2026-09 Alpaca store holds ~1,470, which is the
re-run §12 asked for.

LAYERS, FIXED BEFORE ANY OUTCOME WAS LOOKED AT (30m bars, at the trigger)
    above 200 EMA      price > ema200                      long-term regime
    swing stack        price > ema21 > ema50               intermediate trend
    short momentum     price > ema9                        short-term
    RSI healthy        50 <= rsi14 < 70                    momentum, not stretched
    RSI stretched      rsi14 >= 70                         the "overbought" read
    MACD positive      macd_histogram > 0                  confirmation
    volume             volume_ratio >= 1.5                 participation
    evidence score     count of: above 200, swing stack, RSI healthy,
                       MACD positive, volume  -> 0-1 / 2-3 / 4-5

Eight comparisons, so the bar is p < 0.05/8 = 0.006 for any single one.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.backtest.setups import resolve
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED
from scanner.features.intraday_context import compute as context_of
from scanner.screens.smc import scan

from relative_strength_test import permutation_p

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
TESTS = 8
ALPHA = 0.05 / TESTS


def layers(ctx) -> dict[str, bool | str]:
    p = ctx.price

    def gt(a, b):
        return a is not None and b is not None and a > b

    out = {
        "above 200 EMA": gt(p, ctx.ema200),
        "swing stack": gt(p, ctx.ema21) and gt(ctx.ema21, ctx.ema50),
        "short momentum": gt(p, ctx.ema9),
        "RSI healthy": ctx.rsi14 is not None and 50 <= ctx.rsi14 < 70,
        "RSI stretched": ctx.rsi14 is not None and ctx.rsi14 >= 70,
        "MACD positive": ctx.macd_histogram is not None and ctx.macd_histogram > 0,
        "volume": ctx.volume_ratio is not None and ctx.volume_ratio >= 1.5,
    }
    score = sum(out[k] for k in ("above 200 EMA", "swing stack", "RSI healthy",
                                 "MACD positive", "volume"))
    out["evidence score"] = "0-1" if score <= 1 else "2-3" if score <= 3 else "4-5"
    return out


def stats(values: list[float]) -> str:
    if not values:
        return f"{'n=0':>26}"
    mean = statistics.fmean(values)
    tint = GREEN if mean > 0 else RED
    win = sum(v > 0 for v in values) / len(values) * 100
    return f"{tint}{mean:>+7.3f}R{RESET} n={len(values):<4} win {win:>3.0f}%"


def compare(name, train, hold, groups, min_group, draws):
    """Train picks the better group; the holdout tests that pick unchanged."""
    t = {g: [r for k, r in train if k == g] for g in groups}
    h = {g: [r for k, r in hold if k == g] for g in groups}
    print(f"\n  {BOLD}{name}{RESET}")
    for g in groups:
        print(f"    {str(g):<7} train {stats(t[g])}   holdout {stats(h[g])}")
    usable = [g for g in groups if len(t[g]) >= min_group]
    if len(usable) < 2:
        print(f"    {YELLOW}too thin to choose on train{RESET}")
        return
    best = max(usable, key=lambda g: statistics.fmean(t[g]))
    worst = min(usable, key=lambda g: statistics.fmean(t[g]))
    a, b = h[best], h[worst]
    if len(a) < min_group or len(b) < min_group:
        print(f"    {YELLOW}holdout too thin ({len(a)} vs {len(b)}){RESET}")
        return
    train_gap = statistics.fmean(t[best]) - statistics.fmean(t[worst])
    gap = statistics.fmean(a) - statistics.fmean(b)
    p = permutation_p(list(a), list(b), draws)
    verdict = (f"{RED}SIGN FLIPPED{RESET}" if (gap > 0) != (train_gap > 0)
               else f"{GREEN}SURVIVES p<{ALPHA:.3f}{RESET}" if p < ALPHA
               else f"{DIM}same direction, not significant{RESET}")
    print(f"    train picked {best} over {worst} ({train_gap:+.3f}R); "
          f"holdout {gap:+.3f}R p={p:.3f}  {verdict}")


def main(store: str, holdout: float, draws: int, min_group: int) -> int:
    path = Path(store)
    history.STORE_DIR = path
    rows = []
    for symbol in sorted({f.name.split("__")[0] for f in path.glob("*.json")}):
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 210:
            continue
        for setup in scan(bars, k=V.swing_k, require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct, min_reward_risk=V.min_reward_risk):
            i = setup.trigger_index
            if crosses_session(bars, i) or setup.entry_price - setup.stop_price <= 0:
                continue
            if bars.times[i].weekday() >= V.wind_down_from_weekday:
                continue
            outcome = resolve(bars, symbol=setup.symbol, entry_index=i,
                              entry_price=setup.entry_price, stop_price=setup.stop_price,
                              target_price=setup.target_price, max_bars=V.max_hold_bars)
            if outcome:
                rows.append((bars.times[i], layers(context_of(bars.upto(i))),
                             outcome.r_multiple))
    rows.sort(key=lambda row: row[0])
    n = len(rows)
    if n < 100:
        print(f"{YELLOW}only {n} scored{RESET}")
        return 0
    cut = int(n * (1 - holdout))
    print(f"{DIM}{n} setups (Mon-Thu, policy {V.fingerprint}); train {cut} "
          f"{rows[0][0]:%Y-%m-%d}..{rows[cut - 1][0]:%Y-%m-%d}, holdout {n - cut} "
          f"..{rows[-1][0]:%Y-%m-%d}; bar p<{ALPHA:.3f} ({TESTS} tests){RESET}")
    for name in ("above 200 EMA", "swing stack", "short momentum", "RSI healthy",
                 "RSI stretched", "MACD positive", "volume"):
        train = [(row[1][name], row[2]) for row in rows[:cut]]
        hold = [(row[1][name], row[2]) for row in rows[cut:]]
        compare(name, train, hold, (True, False), min_group, draws)
    train = [(row[1]["evidence score"], row[2]) for row in rows[:cut]]
    hold = [(row[1]["evidence score"], row[2]) for row in rows[cut:]]
    compare("evidence score (layers agreeing, of 5)", train, hold,
            ("0-1", "2-3", "4-5"), min_group, draws)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="data/intraday_alpaca")
    parser.add_argument("--holdout", type=float, default=0.333)
    parser.add_argument("--draws", type=int, default=20_000)
    parser.add_argument("--min-group", type=int, default=20)
    a = parser.parse_args()
    raise SystemExit(main(a.store, a.holdout, a.draws, a.min_group))
