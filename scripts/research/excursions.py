"""How far did each trade go against you before it worked?

    python scripts/excursions.py
    python scripts/excursions.py --policy fb709122e949

READ-ONLY research. It resolves completed trades against stored bars and
prints. It places no order and changes no policy.

WHAT MAE AND MFE ARE, AND WHY THIS REPO LACKED THEM
Every record here keeps an entry and an exit and NOTHING in between. That
makes a stop that was grazed indistinguishable from one that was never
threatened, and a loser that first ran most of the way to target
indistinguishable from one that went straight down.

  MAE  maximum adverse excursion — the worst point against the entry
  MFE  maximum favourable excursion — the best point in its favour

Both in R, so they sit on the same scale as the result.

WHAT THEY ANSWER HERE
Replay showed live fills beating the bars by +0.172R per trade, which on a
PAPER account is optimistic fill simulation rather than edge. Excursions are
the check: if winners routinely survive an MAE near -0.9R, the edge is
depending on stops that barely held, and a real fill would have taken them
out. That is a different and worse story than "the strategy works".

They also price the exits. A loser whose MFE reached +1.5R was a winner that
was given back, and no stop placement fixes that — only an exit rule does.

BORROWED, NOT COPIED
The idea comes from `core/risk/exits.py` in the LLM-Quant-Engine project on
this machine, which tracks excursions on every open position. That project is
crypto and its numbers do not transfer; the mechanism does.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution.policy import TradingPolicy
from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

POLICY = TradingPolicy()


def _rows(path: str) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        pass
    return out


def _bars(symbol: str, minutes: int):
    try:
        return history.accumulated(symbol, minutes, SESSION_ALIGNED)
    except Exception:                                   # noqa: BLE001
        return None


def _index_at(bars, stamp: str) -> int | None:
    try:
        wanted = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    for i, t in enumerate(bars.times):
        if t == wanted:
            return i
    return None


def excursions(bars, entry_index: int, entry: float, risk: float,
               max_bars: int, stop: float, target: float):
    """Walk forward to the exit, tracking the extremes on the way.

    Stops at whichever comes first — stop touched, target touched, or the
    time limit — so the excursions describe the trade that happened rather
    than the whole series after it.
    """
    worst = best = 0.0
    last = min(entry_index + max_bars, len(bars) - 1)
    for i in range(entry_index + 1, last + 1):
        worst = min(worst, (bars.lows[i] - entry) / risk)
        best = max(best, (bars.highs[i] - entry) / risk)
        if bars.lows[i] <= stop or bars.highs[i] >= target:
            break
    return worst, best


def main(path: str, policy: str | None, minutes: int) -> int:
    rows = _rows(path)
    intents = {}
    for row in rows:
        if row.get("event") in ("trade_intent", "order_submitted"):
            intent = row.get("intent") or {}
            if intent.get("client_order_id"):
                intents[intent["client_order_id"]] = intent

    records = []
    for row in rows:
        if row.get("event") != "trade_outcome":
            continue
        if policy and row.get("policy") != policy:
            continue
        out = row.get("outcome") or {}
        intent = intents.get(out.get("client_order_id", ""))
        if not intent:
            continue
        bars = _bars(out.get("symbol", ""), minutes)
        if bars is None:
            continue
        index = _index_at(bars, str(intent.get("trigger_at", "")))
        if index is None:
            continue
        entry = float(intent.get("entry_price") or 0)
        stop = float(intent.get("stop_price") or 0)
        target = float(intent.get("target_price") or 0)
        risk = entry - stop
        if risk <= 0:
            continue
        mae, mfe = excursions(bars, index, entry, risk,
                              POLICY.max_hold_bars, stop, target)
        records.append({
            "symbol": out.get("symbol"), "r": float(out.get("realized_r", 0)),
            "reason": out.get("exit_reason", ""), "mae": mae, "mfe": mfe,
        })

    if not records:
        print(f"{YELLOW}nothing resolvable — the store may not reach back to "
              f"these trigger bars{RESET}")
        return 0

    print(f"{DIM}{len(records)} trade(s) resolved against stored bars{RESET}\n")
    print(f"  {'sym':<6}{'result':>9}{'MAE':>9}{'MFE':>9}  reason")
    for rec in records:
        mae_tint = RED if rec["mae"] <= -0.8 else DIM
        mfe_tint = GREEN if rec["mfe"] >= 1.0 else DIM
        print(f"  {rec['symbol']:<6}{rec['r']:>+9.3f}"
              f"{mae_tint}{rec['mae']:>+9.3f}{RESET}"
              f"{mfe_tint}{rec['mfe']:>+9.3f}{RESET}  {rec['reason']}")

    wins = [r for r in records if r["r"] > 0]
    losses = [r for r in records if r["r"] <= 0]

    print(f"\n{BOLD}WHAT THE STOPS SURVIVED{RESET}")
    if wins:
        deep = [w for w in wins if w["mae"] <= -0.8]
        print(f"  winners            {len(wins)}")
        print(f"  worst MAE on a win {min(w['mae'] for w in wins):+.3f}R")
        print(f"  median MAE on wins {statistics.median(w['mae'] for w in wins):+.3f}R")
        if deep:
            print(f"  {RED}{len(deep)} win(s) came within 0.2R of the stop — "
                  f"a worse fill takes those out{RESET}")
        else:
            print(f"  {GREEN}no winner came within 0.2R of its stop{RESET}")

    print(f"\n{BOLD}WHAT THE LOSERS GAVE BACK{RESET}")
    if losses:
        gave = [l for l in losses if l["mfe"] >= 1.0]
        print(f"  losers             {len(losses)}")
        print(f"  best MFE on a loss {max(l['mfe'] for l in losses):+.3f}R")
        if gave:
            print(f"  {YELLOW}{len(gave)} loss(es) first reached +1R or better "
                  f"— those are exit-rule losses, not stop-placement ones"
                  f"{RESET}")
        else:
            print(f"  {GREEN}no loser ever reached +1R{RESET}")

    print(f"\n{DIM}n={len(records)}. Nothing here is a finding; it is a "
          f"description of what happened. Read it for the SHAPE of the "
          f"trades, not for a number to act on.{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--log", default=settings.audit_log_path)
    p.add_argument("--policy", default=None)
    p.add_argument("--minutes", type=int, default=POLICY.interval_minutes)
    a = p.parse_args()
    raise SystemExit(main(a.log, a.policy, a.minutes))
