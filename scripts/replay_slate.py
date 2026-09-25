"""Score the candidates that were never bought.

    python scripts/replay_slate.py --validate     # check replay fidelity first
    python scripts/replay_slate.py                # score recorded slates

READ-ONLY, and outside the frozen trading path. It reads the audit log and the
accumulated bar store, resolves trades that never happened, and prints.

THE PROBLEM IT EXISTS FOR
Outcomes exist only for trades that were TAKEN, and trades are taken from the
top of the ranking. The model's third-choice calls never become orders, never
become outcomes, and therefore never become evidence — its ranking cannot be
shown wrong by any quantity of live trading. That is survivorship, and unlike
small sample size it does not shrink with time.

`candidate_slate` records break it by writing every ranked candidate, taken or
not, with its levels and its trigger bar. A candidate ranked third has a
DETERMINED outcome sitting in bars already held. This resolves it.

VALIDATE BEFORE YOU BELIEVE IT
`--validate` replays the trades that really happened and compares the result
against the realized R the broker reported. If replay cannot reproduce a trade
whose answer is known, it cannot be trusted on trades whose answer is not.
Expect small differences and read them: the broker fills at a real price,
while replay assumes the intended entry, so the gap between them IS the
execution cost the backtest could not measure.

WHAT IT CANNOT DO
It cannot re-run the model on history. Ranking reads headlines, and headlines
are never persisted — `NewsItem.fetched_at` is documented as a point-in-time
stamp for backtests and nothing writes it to disk. So a prompt change cannot
be tested against the past, only forward. Scoring the ranking needs only its
OUTPUT, which the slate has; re-running it would need the INPUT, which nobody
kept.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime

from scanner.backtest.setups import resolve
from scanner.execution.policy import TradingPolicy
from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

POLICY = TradingPolicy()


def load_rows(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def bars_for(symbol: str, minutes: int):
    """Stored bars, or None when the store has nothing usable."""
    try:
        rows = history.load(symbol, minutes, SESSION_ALIGNED)
        return history.to_bars(symbol, minutes, SESSION_ALIGNED, rows)
    except Exception:
        return None


def entry_index_at(bars, stamp: str) -> int | None:
    """Index of the bar the setup triggered on.

    Matched on the bar's own timestamp rather than by searching for a price:
    two bars can share a price and only one is the trigger.
    """
    try:
        wanted = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    for i, t in enumerate(bars.times):
        if t == wanted:
            return i
    return None


def replay_one(entry: dict, minutes: int, max_bars: int):
    """Resolve one candidate. None when it cannot be resolved honestly."""
    symbol = entry.get("symbol", "")
    bars = bars_for(symbol, minutes)
    if bars is None:
        return None, "no stored bars"
    index = entry_index_at(bars, entry.get("trigger_at", ""))
    if index is None:
        return None, "trigger bar not in store"
    outcome = resolve(
        bars, symbol=symbol, entry_index=index,
        entry_price=float(entry.get("entry_price", 0)),
        stop_price=float(entry.get("stop_price", 0)),
        target_price=float(entry.get("target_price", 0)),
        max_bars=max_bars,
    )
    if outcome is None:
        return None, "still open at the end of the series"
    return outcome, ""


def validate(rows: list[dict], minutes: int, max_bars: int) -> int:
    """Replay trades whose real answer is known."""
    intents = {}
    for row in rows:
        if row.get("event") in ("trade_intent", "order_submitted"):
            i = row.get("intent") or {}
            if i.get("client_order_id"):
                intents[i["client_order_id"]] = i

    print(f"{BOLD}REPLAY FIDELITY{RESET} {DIM}— trades whose answer is "
          f"already known{RESET}\n")
    print(f"  {'sym':<6}{'broker R':>10}{'replay R':>10}{'diff':>9}  reason")
    deltas = []
    checked = skipped = 0
    for row in rows:
        if row.get("event") != "trade_outcome":
            continue
        out = row.get("outcome") or {}
        coid = out.get("client_order_id")
        intent = intents.get(coid or "")
        if not intent:
            continue
        entry = {
            "symbol": out.get("symbol"),
            "trigger_at": intent.get("trigger_at", ""),
            "entry_price": intent.get("entry_price", 0),
            "stop_price": intent.get("stop_price", 0),
            "target_price": intent.get("target_price", 0),
        }
        replayed, why = replay_one(entry, minutes, max_bars)
        actual = float(out.get("realized_r", 0.0))
        if replayed is None:
            skipped += 1
            print(f"  {str(out.get('symbol')):<6}{actual:>10.3f}"
                  f"{'—':>10}{'—':>9}  {DIM}{why}{RESET}")
            continue
        checked += 1
        diff = replayed.r_multiple - actual
        deltas.append(diff)
        colour = GREEN if abs(diff) < 0.25 else YELLOW
        print(f"  {str(out.get('symbol')):<6}{actual:>10.3f}"
              f"{replayed.r_multiple:>10.3f}{colour}{diff:>+9.3f}{RESET}"
              f"  {replayed.reason}")

    if deltas:
        mean = statistics.mean(deltas)
        print(f"\n  checked {checked}, skipped {skipped}")
        print(f"  mean difference {mean:+.3f}R")
        if mean > 0:
            print(f"{DIM}  Replay is OPTIMISTIC against live fills. That gap "
                  f"is execution cost the backtest could not measure.{RESET}")
        else:
            print(f"{DIM}  Replay is PESSIMISTIC: live did BETTER than the "
                  f"bars say it should have.{RESET}")
            print(f"{DIM}  Read the stops. Replay scores every stop at "
                  f"exactly -1.000R because it assumes entry at the INTENDED "
                  f"price. Live entries are limit orders that filled better "
                  f"than intended, which shrinks the loss in R terms — PSX "
                  f"intended 262.83 and filled 259.74.{RESET}")
            print(f"{DIM}  On a PAPER account treat that as flattery, not "
                  f"edge: FREEZE.md already records paper fills running "
                  f"+1.50 optimistic against the stop level. Replay is the "
                  f"conservative number and the one to plan with.{RESET}")
    else:
        print(f"\n  {YELLOW}nothing could be replayed — the store does not "
              f"reach back to these trigger bars{RESET}")
    return 0


def score(rows: list[dict], minutes: int, max_bars: int, min_group: int) -> int:
    slates = [r for r in rows if r.get("event") == "candidate_slate"]
    print(f"{DIM}{len(slates)} slate record(s){RESET}")
    if not slates:
        print(f"\n{YELLOW}No slates recorded yet.{RESET}")
        print(f"{DIM}Slate recording landed 2026-09-24; the first scan to "
              f"write one is the next one with candidates. Until then there "
              f"is nothing to score, and that is the honest state rather "
              f"than an error.{RESET}")
        return 0

    by_rank: dict[int, list[float]] = defaultdict(list)
    unresolved = 0
    taken_ranks: list[int] = []
    for record in slates:
        for entry in record.get("slate") or []:
            outcome, _ = replay_one(entry, minutes, max_bars)
            if outcome is None:
                unresolved += 1
                continue
            rank = int(entry.get("llm_rank") or 0)
            by_rank[rank].append(outcome.r_multiple)
            taken_ranks.append(rank)

    print(f"\n{BOLD}REALIZED R BY THE MODEL'S RANK{RESET} "
          f"{DIM}(including candidates never bought){RESET}")
    for rank in sorted(by_rank):
        values = by_rank[rank]
        mean = statistics.mean(values)
        flag = (f"{GREEN}n={len(values)}{RESET}" if len(values) >= min_group
                else f"{YELLOW}n={len(values)} UNDECIDABLE{RESET}")
        print(f"  rank {rank:<3} {mean:+.3f}R   {flag}")
    if unresolved:
        print(f"  {DIM}{unresolved} candidate(s) unresolved{RESET}")

    print(f"\n{DIM}A rank that does not separate outcomes is a rank that is "
          f"not ranking. Before acting on any ordering here it needs a "
          f"permutation test and data it was not chosen from.{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--log", default="logs/trading_audit.jsonl")
    p.add_argument("--minutes", type=int, default=POLICY.interval_minutes)
    p.add_argument("--max-bars", type=int, default=POLICY.max_hold_bars)
    p.add_argument("--min-group", type=int, default=5)
    p.add_argument("--validate", action="store_true",
                   help="replay trades whose real outcome is known")
    a = p.parse_args()
    data = load_rows(a.log)
    raise SystemExit(
        validate(data, a.minutes, a.max_bars) if a.validate
        else score(data, a.minutes, a.max_bars, a.min_group)
    )
