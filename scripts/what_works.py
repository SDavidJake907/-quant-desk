"""What is actually working — joined from decisions to outcomes.

    python scripts/what_works.py
    python scripts/what_works.py --policy fb709122e949   # one window only
    python scripts/what_works.py --min-group 5

READ-ONLY, and outside the frozen trading path. It reads the audit log and
prints. It places no order and changes no policy.

WHAT THIS IS
Every decision this system makes already records the LLM's judgment
(`llm_rank`, `llm_conviction`, `llm_driver`) beside the deterministic ranking
and eight shadow dimensions — RSI gate, session slot, weekday, sector,
liquidity tier. `trade_outcome` records now close the loop with realized R.
This joins the two by `client_order_id` and asks, of every recorded dimension,
whether it separated winners from losers.

That is the machinery for "is Gemma's judgment worth acting on" — the question
`TradingPolicy` has been carrying four `shadow_*` flags to answer, and which
was unanswerable until outcomes started being written.

WHY IT REFUSES TO ANSWER MOST OF THE TIME
This project's expensive lesson was thirteen ablation variants, best-of
selection, +0.429R on train and +0.014R on holdout. A tool that cheerfully
reports the best-looking split of nine trades would rebuild that mistake with
a nicer interface.

So every group carries its n, groups below `--min-group` are reported as
UNDECIDABLE rather than ranked, and a dimension where every trade has the same
value is called DEGENERATE — it recorded nothing to compare. The output is
designed to be boring until there is enough evidence for it not to be.

A SPLIT THAT LOOKS GOOD HERE IS NOT A FINDING. It is a hypothesis that needs a
permutation test and a holdout, which is what `screen_ablation.py` is for.

THE SELECTION PROBLEM, WHICH IS WORSE THAN THE SAMPLE SIZE
Outcomes exist only for trades that were TAKEN, and trades are taken from the
top of the ranking. So every `llm_rank` group below is drawn from a sample that
was filtered by rank in the first place.

Across all 94 recorded decisions the model's ranks span 1 to 4 and disagree
with the deterministic rank 52% of the time — it is producing real, independent
judgment. But only ranks 1 and 2 ever reach an outcome, because nothing else is
ever bought. **No quantity of live trading can tell you whether the model's
low ranks were right, because those trades never happen.**

This is survivorship, the same shape as the delisted-history problem in
WORKLIST §4, and it does not shrink as trades accumulate. Answering "is the
model's ranking any good" needs the setups it rejected, replayed offline
against outcomes already known — not more live trades.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

#: Dimensions recorded on every decision. The `llm_*` ones are the model's own
#: judgment; the rest are the deterministic context it is being judged against.
DIMENSIONS = (
    ("llm_rank", "the model's ranking"),
    ("llm_conviction", "the model's conviction"),
    ("llm_driver", "what the model said drove it"),
    ("priority_rank", "deterministic rank"),
    ("rsi_gate_passed", "RSI shadow gate"),
    ("trigger_slot", "session slot"),
    ("trigger_weekday", "weekday"),
    ("liquidity_tier", "liquidity tier"),
    ("sector", "sector"),
)


def load(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def join(rows: list[dict], policy: str | None) -> list[tuple[dict, dict, dict]]:
    """Pair each outcome with the decision that produced it.

    Keyed on `client_order_id` because that is what attributes a fill to this
    system. A trade with no matching decision is someone's manual trade and is
    dropped rather than pooled.
    """
    intents: dict[str, tuple[dict, dict]] = {}
    for row in rows:
        if row.get("event") not in ("trade_intent", "order_submitted"):
            continue
        intent = row.get("intent") or {}
        coid = intent.get("client_order_id")
        if coid:
            intents[coid] = (row, intent)

    paired = []
    for row in rows:
        if row.get("event") != "trade_outcome":
            continue
        outcome = row.get("outcome") or {}
        coid = outcome.get("client_order_id")
        if not coid or coid not in intents:
            continue
        decision, intent = intents[coid]
        if policy and decision.get("policy") != policy:
            continue
        paired.append((decision, intent, outcome))
    return paired


def _summarise(values: list[float]) -> str:
    mean = statistics.mean(values)
    if len(values) > 1:
        err = statistics.stdev(values) / len(values) ** 0.5
        return f"{mean:+.3f}R ± {err:.3f}"
    return f"{mean:+.3f}R"


def report_dimension(
    paired: list[tuple[dict, dict, dict]], field: str, label: str, min_group: int,
) -> None:
    groups: dict[str, list[float]] = defaultdict(list)
    for _, intent, outcome in paired:
        value = intent.get(field)
        if value is None or value == "":
            continue
        groups[str(value)].append(float(outcome.get("realized_r", 0.0)))

    print(f"\n{BOLD}{label}{RESET} {DIM}({field}){RESET}")
    if not groups:
        print(f"  {DIM}not recorded on any joined trade{RESET}")
        return
    if len(groups) == 1:
        only = next(iter(groups))
        print(f"  {YELLOW}DEGENERATE{RESET} — every trade has {field}={only}. "
              f"Nothing was compared.")
        return

    for value, results in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        n = len(results)
        flag = (f"{GREEN}n={n}{RESET}" if n >= min_group
                else f"{YELLOW}n={n} UNDECIDABLE{RESET}")
        print(f"  {value:<14} {_summarise(results):<20} {flag}")

    if all(len(v) < min_group for v in groups.values()):
        print(f"  {DIM}every group is below --min-group; the ordering above is "
              f"noise and must not be read as a ranking{RESET}")


def main(path: str, policy: str | None, min_group: int) -> int:
    rows = load(path)
    paired = join(rows, policy)

    print(f"{DIM}{len(rows)} audit records · {len(paired)} decisions joined to "
          f"outcomes{'' if not policy else f' · policy {policy}'}{RESET}")
    if not paired:
        print(f"{YELLOW}nothing to report — no outcome is linked to a decision "
              f"this system made{RESET}")
        return 0

    values = [float(o.get("realized_r", 0.0)) for _, _, o in paired]
    wins = sum(1 for v in values if v > 0)
    print(f"\n{BOLD}OVERALL{RESET}")
    print(f"  expectancy   {_summarise(values)}")
    print(f"  win rate     {wins}/{len(values)}")

    for field, label in DIMENSIONS:
        report_dimension(paired, field, label, min_group)
        if field == "llm_rank":
            print(f"  {YELLOW}SELECTED SAMPLE{RESET} — only high-ranked "
                  f"candidates are ever bought, so low ranks cannot appear "
                  f"here")
            print(f"  {DIM}whatever this shows, it cannot answer whether the "
                  f"model's ranking works{RESET}")

    print(f"\n{BOLD}WHAT THIS DOES NOT TELL YOU{RESET}")
    print(f"{DIM}  Nothing above is a finding. A split that looks good on this{RESET}")
    print(f"{DIM}  many trades is a hypothesis, and this project has already paid{RESET}")
    print(f"{DIM}  for the difference once: thirteen variants, best-of chosen,{RESET}")
    print(f"{DIM}  +0.429R on train and +0.014R on holdout.{RESET}")
    print(f"{DIM}  Before acting on any row here it needs a permutation test and{RESET}")
    print(f"{DIM}  data it was not chosen from.{RESET}")
    if len(paired) < 30:
        print(f"\n  {YELLOW}{len(paired)} trades. The 30-trade checkpoint in "
              f"FREEZE.md exists because this is not yet a sample.{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--log", default="logs/trading_audit.jsonl")
    p.add_argument("--policy", default=None,
                   help="only trades carrying this policy fingerprint")
    p.add_argument("--min-group", type=int, default=5,
                   help="below this a group is reported UNDECIDABLE")
    a = p.parse_args()
    raise SystemExit(main(a.log, a.policy, a.min_group))
