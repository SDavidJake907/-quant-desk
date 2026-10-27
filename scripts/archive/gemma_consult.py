"""Hand the model the whole day and ask what it would change.

    python scripts/gemma_consult.py
    python scripts/gemma_consult.py --model qwen2.5:14b-instruct

READ-ONLY. It assembles facts, asks, and prints. It changes nothing.

HOW THIS DIFFERS FROM gemma_audit.py
`gemma_audit.py` asks questions whose answers are already settled and marks the
result. That is the only way to learn whether the model is worth listening to,
and it scores 2/2.

This asks OPEN questions, which cannot be marked. That is a weaker instrument
and the output should be read as an argument to evaluate, not an answer to
adopt. It is worth doing because the model has twice reached conclusions here
before the measurement did -- it named the outlier trade skewing the mean and
the optimistic fill assumption unprompted, both later confirmed.

THE FACTS ARE GIVEN STRAIGHT
Including the unflattering ones. A model handed a curated brief will produce a
curated answer, so the brief below carries the null results, the trial count
that undermines the one surviving result, and the live positions currently
underwater.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.llm.ollama import chat_json

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, YELLOW, CYAN = "\033[32m", "\033[33m", "\033[36m"

FACTS = """
WHAT THIS SYSTEM IS
A deterministic screen for a liquidity-sweep pattern on 30-minute bars of S&P
500 stocks: price sweeps a prior swing low, makes a higher low, breaks
character, then breaks structure. Entry at the close of the break bar, stop at
the higher low, target at the highest swing high before the sweep. Long only.
Paper trading through a broker with bracket orders.

THE CENTRAL MEASUREMENT
Over two years of independent data, 861 setups, the configuration actually
traded earns +0.000R per trade. Standard error 0.058, so the true value is
bounded roughly within +/- 0.11R. Not proof of no edge; fairly strong evidence
of no LARGE edge.

WHAT THE FILTERS CONTRIBUTE, measured by stacking them one at a time:
  raw pattern, no filters        12259 setups   -0.027R
  + price >= $5                  12259         -0.027R
  + dollar volume >= 20M          6488         -0.032R
  + stop distance >= 1.5%         1889         +0.084R
  + reward:risk >= 1.5             528         +0.264R
So the lift comes from RISK GEOMETRY, not from stock selection. 96% of raw
setups are discarded to get there.

TWENTY-TWO IDEAS TESTED, TWENTY-TWO NULLS
Thirteen tried to rank one setup against another; all flipped sign between
train and holdout. Also null: delayed entry, four horizons, sweep rejection
shape, take-profit ladders, a bearish-divergence exit, a structural lower-low
exit, gap risk, relative strength, higher-timeframe liquidity pools, an
accumulation signal from another project, and VIX regime.

Two things did NOT die, and both are statements about market state rather than
stock quality:
  20M liquidity floor   p=0.022
  S&P regime filter     p=0.201, below-both -0.086R n=179, and it HELD its
                        value when the sample grew 57%

THE PROBLEM WITH THAT
Declared trials are near seventy. A Bonferroni threshold sits around 0.0007.
The 20M floor at p=0.022 does not clear it. The one result this project calls
validated does not survive its own multiple-comparisons correction.

THE PATTERN THAT SHOWS UP IN EVERY SINGLE TEST
Train half negative, holdout half positive. Seven hold-duration arms, five
timeframes, four VIX bands, four relative-strength bands -- all of them. The
variance between the two halves of the sample is larger than the variance
between any strategy variant that can be constructed.
One cause is identified: the halves saw different markets. Train had 48% of
setups in elevated/stressed volatility, holdout 24%.

WHAT IS CLOSED, with measurements
  bigger stocks    all 22 mega-caps already in the universe
  more stocks      all 1504 symbols adds 20 tradeable names
  smaller stocks   $1-5B companies fail the liquidity floor by 10x
  finer bars       15m gives 0.56x the setups and worse results
  coarser bars     90m/120m/240m significantly worse
  day trading      -0.075R, only 1% of trades reach target
  weekday effects  rankings reordered completely between samples

WHAT IS NOT KNOWN
  fill rate        the backtest assumes a GTC limit always fills; never measured
  bear markets     the validated sample contains 0 down-trending sessions
  the model itself its per-candidate ranking has never been scored; 3 slates exist

LIVE STATE
Paper account 101,686. Ten closed trades: +4.774R total, mean +0.477R, 40% win
rate, +1,860 dollars, one trade (+3.197R) carrying most of the mean. Two open
positions, both currently negative, both opened this morning under rules that
have since changed -- both were deep laggards and a trend gate added today would
now block them.
Risk per trade is 0.435% of equity against a declared 1.0%, because a 20%
position cap binds on 97.2% of trades.

WHAT CHANGED TODAY
Deployment raised from a 56% weekday average to a flat 100%. Friday entries
enabled, having been excluded on a flatten cost of -0.213R that measured
-0.012R. A relative-strength floor added, which TESTED NULL and was enabled on
the operator's judgement. The model given price context and a veto.
Together these roughly double dollar throughput, and roughly double drawdown.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "state": {"type": "string", "enum": ["promising", "unproven",
                                             "probably_no_edge"]},
        "biggest_missing_thing": {"type": "string"},
        "most_likely_mistake": {"type": "string"},
        "top_priority": {"type": "string"},
        "what_to_stop": {"type": "string"},
        "fund_it_yet": {"type": "string", "enum": ["yes", "no", "not_yet"]},
        "reasoning": {"type": "string"},
    },
    "required": ["state", "biggest_missing_thing", "most_likely_mistake",
                 "top_priority", "what_to_stop", "fund_it_yet", "reasoning"],
}

PROMPT = """You are advising the operator of an automated trading system. You
have its complete current state below, including the results that do not
flatter it.

The operator has spent months on this and is frustrated that it is not
profitable. Do not manage that feeling. Tell them what the facts support.

Answer:
- state: is this promising, unproven, or probably without an edge?
- biggest_missing_thing: what is absent that would most change the picture?
- most_likely_mistake: where is the reasoning above most likely to be wrong?
  Argue against the analysis, not for it.
- top_priority: the single next action, and it must be concrete.
- what_to_stop: what is being done that is wasted effort or actively harmful?
- fund_it_yet: should real money go in?
- reasoning: two or three sentences. No numbers you were not given.

FACTS:
{facts}
"""


async def main(model: str) -> int:
    print(f"{DIM}asking {model} for an open assessment "
          f"({len(FACTS.split())} words of context)...{RESET}\n")
    async with httpx.AsyncClient(timeout=600) as client:
        try:
            out = await chat_json(PROMPT.format(facts=FACTS), SCHEMA, client,
                                  model=model)
        except Exception as exc:                             # noqa: BLE001
            print(f"  {YELLOW}model unavailable: {exc}{RESET}")
            return 1

    def line(key: str, label: str) -> None:
        value = str(out.get(key, "")).strip()
        print(f"  {BOLD}{label}{RESET}")
        for chunk in [value[i:i + 74] for i in range(0, len(value), 74)]:
            print(f"    {chunk}")
        print()

    print(f"  {BOLD}VERDICT{RESET}  {CYAN}{out.get('state')}{RESET}"
          f"   fund it: {CYAN}{out.get('fund_it_yet')}{RESET}\n")
    line("biggest_missing_thing", "BIGGEST MISSING THING")
    line("most_likely_mistake", "WHERE THIS ANALYSIS IS MOST LIKELY WRONG")
    line("top_priority", "TOP PRIORITY")
    line("what_to_stop", "STOP DOING")
    line("reasoning", "REASONING")
    print(f"  {DIM}An open question cannot be marked. Read this as an argument "
          f"to weigh, not an answer to adopt — the graded instrument is "
          f"gemma_audit.py, where it scores 2/2.{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=settings.triage_model)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.model)))
