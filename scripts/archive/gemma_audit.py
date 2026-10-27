"""Give the model everything, then mark its work.

    python scripts/gemma_audit.py
    python scripts/gemma_audit.py --model qwen2.5:14b-instruct

READ-ONLY research. It assembles facts, asks the model to reason over them,
and prints its answers beside the ones already established. It changes
nothing and decides nothing.

WHY THIS AND NOT A DEMO
"Let the model see all the data" is easy to do and worthless to watch: a
fluent paragraph about a trading system reads as competent whether or not it
is right. The only way to learn anything is to ask questions whose answers are
already known and then mark the result.

So every question below has a settled answer, established by measurement
earlier and recorded in FREEZE.md and WORKLIST.md:

  health            running, but the strategy is unproven
  constraint        setup scarcity, ~2 per session across 499 symbols
  profitable        CANNOT TELL — 10 trades, the 95% interval contains zero,
                    and one trade (+3.197R) carries most of the mean
  priority          let it run untouched; verify the cache fix doubles
                    candidate conversion

A model that reaches those from the facts has earned more responsibility. One
that confabulates a verdict the data does not support has argued against
itself, and that is worth knowing BEFORE wiring it to anything.

NO NUMBERS, SAME AS EVERYWHERE ELSE
The answer schema is enums and short prose. The figures are supplied TO the
model and never come back from it, so a hallucinated statistic cannot enter
the page. That is the rule the news layer runs under and the reason its
verdicts are trustworthy enough to block a trade.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution.policy import TradingPolicy
from scanner.llm.ollama import LLMError, chat_json

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

POLICY = TradingPolicy()

#: What was actually established, by measurement, earlier today.
#:
#: `health` is deliberately NOT graded, and the first run is why. It was
#: keyed to "running"; gemma3:12b answered "degraded" and was marked wrong.
#: Reading the facts it was given — three defects found in a single day and a
#: thirty-day outage nothing detected — "degraded" is at least as defensible
#: as the key was. A test whose answer key is arguable measures its author,
#: so this one is reported and not scored.
TRUTH = {
    "constraint": "setup_scarcity",
    "profitable": "cannot_tell",
}

#: Asked and shown, never marked.
UNGRADED = ("health",)

SCHEMA = {
    "type": "object",
    "properties": {
        "health": {"type": "string",
                   "enum": ["running", "degraded", "broken"]},
        "constraint": {"type": "string",
                       "enum": ["setup_scarcity", "data_quality",
                                "gates_too_strict", "capital", "unknown"]},
        "profitable": {"type": "string",
                       "enum": ["yes", "no", "cannot_tell"]},
        "reasoning": {"type": "string"},
        "top_priority": {"type": "string"},
        "biggest_risk": {"type": "string"},
    },
    "required": ["health", "constraint", "profitable", "reasoning",
                 "top_priority", "biggest_risk"],
}


def facts() -> str:
    rows = []
    try:
        with open(settings.audit_log_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        pass

    outs = [r for r in rows if r.get("event") == "trade_outcome"]
    values = [r["outcome"]["realized_r"] for r in outs]
    events = Counter(r.get("event") for r in rows)
    refusals = Counter(r.get("reason") for r in rows
                       if r.get("event") == "candidate_refused")

    lines = [
        "SYSTEM: an automated paper-trading scanner for US equities.",
        "It scans 499 S&P 500 symbols on 30-minute bars every 30 minutes",
        "during market hours, finds a structural setup (liquidity sweep,",
        "change of character, break of structure), and sends bracket orders",
        "to a paper account. A local language model reviews news and can",
        "veto a trade; it does not choose or size trades.",
        "",
        f"POLICY: {POLICY.describe()}",
        f"  deployment by weekday: {POLICY.weekday_deployment_pct}",
        f"  max positions {POLICY.max_positions}, risk {POLICY.risk_pct}% per trade",
        f"  freshness gate: a setup is tradeable for {POLICY.fresh_bars} bars",
        f"  max hold {POLICY.max_hold_bars} bars (~4.6 sessions)",
        "",
        "SCAN FUNNEL, measured over the whole log:",
        "  114 scans have run across 14 weekday sessions",
        "  8,479 setup-detections, but setups are RE-COUNTED every scan",
        "  196 unique setups exist over 96 sessions = 2.04 per session",
        "  13 candidates ever reached the order stage",
        "  120 scans produced 0 candidates, 13 produced exactly 1,",
        "  and NO scan has ever produced 2 or more",
        "  99.7% of setup-detections are rejected as STALE (older than 2 bars)",
        "",
        "RESULTS:",
        f"  {len(outs)} completed trades in the system's entire life",
    ]
    if len(values) > 1:
        mean = statistics.fmean(values)
        se = statistics.stdev(values) / len(values) ** 0.5
        best = max(values)
        without = statistics.fmean([v for v in values if v != best])
        lines += [
            f"  mean {mean:+.3f}R, standard error {se:.3f}",
            f"  95% confidence interval {mean - 1.96 * se:+.3f}R to "
            f"{mean + 1.96 * se:+.3f}R",
            f"  the single best trade is {best:+.3f}R; removing it leaves "
            f"{without:+.3f}R",
            "  these trades span 3 different policy versions, so strictly",
            "  they are not one experiment",
            "  a replay against historical bars showed live fills beat the",
            "  bars by +0.172R per trade, which on a PAPER account is",
            "  optimistic fill simulation rather than edge",
        ]
    lines += [
        "",
        "RESEARCH ALREADY DONE (all on held-out data, permutation tested):",
        "  widening the freshness gate was tested: delay does not clearly",
        "  hurt expectancy, but one bar of delay disqualifies over half of",
        "  all setups on the policy's own risk floor, so widening gains little",
        "  shadow dimensions (weekday, liquidity tier, session slot) were",
        "  tested against outcomes: NONE survives, p=0.517 and p=0.798",
        "  the model's own news ranking has never been scored, because only",
        "  trades that were TAKEN produce outcomes and those were chosen by",
        "  a different rule",
        "  historically, 7 of 9 context filters made held-out results WORSE",
        "",
        "KNOWN DEFECTS FOUND AND FIXED TODAY:",
        "  the bar cache renewed its own expiry on every read and served",
        "  data up to 3 bars stale; with a 2-bar freshness gate this could",
        "  lose a setup before the scanner saw it. Fixed.",
        "  the 60-bar hold limit was declared in policy and enforced nowhere.",
        "  Now scheduled.",
        "  the embedding model was never installed, so news deduplication",
        "  had never run once. Fixed.",
        "",
        "OPERATIONAL:",
        f"  audit events: {dict(events)}",
        f"  refusal reasons: {dict(refusals)}",
        "  the host was powered off for 30 days (Aug 24 - Sep 24), missing",
        "  21 trading days. Nothing detected it at the time.",
        "  the evaluation window ends 2026-12-04, fixed in advance, and is",
        "  expected to finish with roughly 20-40 trades",
    ]
    return "\n".join(lines)


PROMPT = """You are reviewing an automated trading system for its operator.
Everything known about it is below. Judge it.

{facts}

health: is the SYSTEM running correctly? This is about the machinery, not
about whether the strategy makes money.

constraint: what most limits how many trades this system takes?

profitable: does the evidence show the STRATEGY makes money? Answer
"cannot_tell" if the sample cannot distinguish profit from chance.

reasoning: two sentences explaining the profitable verdict.
top_priority: the single most valuable thing to do next.
biggest_risk: the thing most likely to cause a wrong conclusion.

Report no numbers, percentages or figures of any kind. They are above; you
are judging, not restating."""


async def main(model: str) -> int:
    body = facts()
    print(f"{DIM}model {model} · {len(body.splitlines())} lines of facts{RESET}\n")

    async with httpx.AsyncClient(timeout=300) as client:
        try:
            raw = await chat_json(PROMPT.format(facts=body), SCHEMA, client,
                                  model=model)
        except (LLMError, ValueError) as exc:
            print(f"{RED}model unreachable: {exc}{RESET}")
            return 1

    print(f"{BOLD}MARKED AGAINST WHAT WAS MEASURED{RESET}")
    score = 0
    for key, expected in TRUTH.items():
        got = str(raw.get(key, "")).lower()
        ok = got == expected
        score += ok
        mark = f"{GREEN}correct{RESET}" if ok else f"{RED}WRONG{RESET}"
        print(f"  {key:<12} said {got:<16} expected {expected:<16} {mark}")
    print(f"\n  {BOLD}{score}/{len(TRUTH)}{RESET}")
    for key in UNGRADED:
        print(f"  {DIM}{key:<12} said {str(raw.get(key, '')).lower():<16} "
              f"not graded — the answer is genuinely arguable{RESET}")

    print(f"\n{BOLD}ITS REASONING{RESET}")
    print(f"  {raw.get('reasoning', '')}")
    print(f"\n{CYAN}top priority:{RESET} {raw.get('top_priority', '')}")
    print(f"{CYAN}biggest risk:{RESET} {raw.get('biggest_risk', '')}")

    print(f"\n{DIM}A model that reaches the measured answers from the facts "
          f"has earned more responsibility. One that asserts a verdict the "
          f"data does not support has argued against itself.{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default=settings.triage_model)
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.model)))
