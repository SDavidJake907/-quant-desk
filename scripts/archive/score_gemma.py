"""Score the model's live calls against realized outcomes.

    python scripts/score_gemma.py                    # every recorded call
    python scripts/score_gemma.py --policy ba95546dbdbb

READ-ONLY. Reads logs/trading_audit.jsonl, prints, writes nothing. Runs after
record_outcomes.py in the daily outcomes job so the score moves the same day a
trade closes. See scanner.llm.scorecard for how calls are joined to trades.
"""

from __future__ import annotations

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.llm.memory import _rows
from scanner.llm.scorecard import MIN_GROUP, Group, score

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
YELLOW = "\033[33m"


def _groups(title: str, groups: tuple[Group, ...]) -> None:
    print(f"{BOLD}{title}{RESET}")
    if not groups:
        print(f"  {DIM}no completed trades to mark yet{RESET}")
        return
    for g in groups:
        flag = "" if g.readable else f"  {DIM}(n<{MIN_GROUP}, unreadable){RESET}"
        print(f"  {g.label:<10} n={g.n:<3} mean {g.mean_r:+.2f}R  "
              f"won {g.wins}/{g.n}{flag}")


def main(policy: str | None) -> int:
    card = score(_rows(settings.audit_log_path), policy=policy)
    scope = f"policy {policy}" if policy else "all policies"
    print(f"{BOLD}GEMMA SCORECARD{RESET}  {DIM}{scope}{RESET}")

    print(f"{BOLD}position reviews{RESET}  {card.reviews_total} calls, "
          f"{card.exit_calls} said exit, {card.reviews_pending} on open positions")
    if card.reviews_total and not card.exit_calls:
        print(f"  {YELLOW}every review said hold -- the call carries no "
              f"information until it sometimes says otherwise{RESET}")
    for t in card.reviewed_trades:
        print(f"  {t.symbol:<6} {t.realized_r:+.2f}R {t.exit_reason:<10} "
              f"{t.reviews} reviews, {t.exits_called} exit, "
              f"last {t.last_confidence}")
    _groups("realized R by last review confidence", card.review_groups)
    _groups("realized R by entry conviction", card.conviction_groups)

    print(f"{BOLD}tradingagents{RESET}  {card.ta_ratings} rating(s), "
          f"{card.ta_errors} failed run(s)")
    _groups("realized R by TradingAgents rating before entry", card.ta_entry_groups)
    _groups("realized R by TradingAgents rating while open", card.ta_during_groups)

    print(f"{BOLD}news gate{RESET}  {len(card.gate_refusals)} model refusal(s), "
          f"failed open on {card.gate_degraded} scan(s)")
    for symbol, day, reason in card.gate_refusals:
        print(f"  {day} {symbol:<6} {reason}")
    if card.gate_refusals:
        print(f"  {DIM}not priced: what each refused name did afterwards is a "
              f"separate measurement{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--policy", default=None,
                   help="only calls made under this policy fingerprint")
    raise SystemExit(main(p.parse_args().policy))
