"""Does a bigger model actually do this job better?

    python scripts/bench_llm.py                       # models already pulled
    python scripts/bench_llm.py --models gemma3:12b mistral-small:24b
    python scripts/bench_llm.py --tier hard           # only the cases that bite

The two jobs this project gives a model are grammar-constrained classification:
read headlines, emit an enum and a verbatim quote. That is not the kind of task
where scale usually pays — the schema already forbids the failure modes a
larger model would be better at avoiding.

So this measures it rather than assuming, on cases with known right answers,
and reports the two things that matter: does it get the call right, and does it
quote text that actually exists. A model that scores the same as a smaller one
is a model whose extra VRAM buys nothing here.

WHY THERE ARE TWO TIERS
The original six cases were all scored 6/6 by gemma3:12b, qwen2.5:14b and
llama3.1:8b alike. A benchmark every candidate passes cannot rank candidates —
it measures nothing and would have waved through a model swap on no evidence,
which is the same error as picking the best of thirteen ablation variants.
Those six are kept as `easy`, as a floor that a broken model would fail, and
the discriminating work is done by `hard`.

The hard cases are built from the ways this classification actually goes wrong
rather than from difficulty for its own sake: a deal the company is only
mentioned near, a company doing the acquiring when a famous headline had it
being acquired, a unit sold rather than the company, a deal called off, an
activist stake that is not a bid, and legal trouble that is not a deal at all.
Each has an unambiguous right answer — contested ground truth would make a
miss meaningless.

GROUNDING IS SCORED HONESTLY
An empty quote counts as grounded ONLY when the answer is "none", where there
is nothing to quote. Previously any empty quote passed, so a model that never
quoted anything scored perfectly on grounding — the metric rewarded silence.

MEASURED, 2026-09-24, on the hard tier
                        correct   grounded   s/call
    gemma3:12b            13/14      14/14     5.08
    qwen2.5:14b-instruct  13/14      14/14     5.45
    llama3.1:8b           12/14      14/14     3.37

The same three models scored 6/6, 6/6, 6/6 on the old easy-only set. The
ranking only appeared once cases existed that could be failed, and it says
llama3.1:8b is worse — so the 40% speed saving and 3.2GB of VRAM it offers
were being bought with accuracy nobody had measured.

READ THE MISSES, NOT ONLY THE SCORE
Every miss by every model was a FALSE POSITIVE: calling a deal where the right
answer was "none". None invented a "none" for a real deal, which is the failure
that would let a takeover target through the gate.

But false positives are not equally harmless, because only `target` blocks:

    gemma3:12b   PFE -> acquirer   does not block. Benign.
    qwen2.5:14b  IP  -> target     BLOCKS International Paper wrongly.
    llama3.1:8b  IP  -> target     BLOCKS. Plus KO -> acquirer, benign.

So by operational cost gemma3:12b made zero false blocks and the other two made
one each. That is a larger gap than 13 versus 12 suggests, and it is the reason
the model was left alone.

IS THERE A SMALLER MODEL? MEASURED 2026-09-24, AND NO
                        size    correct  grounded  s/call  false blocks
    gemma3:12b          8.1GB     13/14     14/14    5.74     0
    llama3.1:8b         4.9GB     12/14     14/14    3.34     1
    qwen2.5:7b-instruct 4.7GB     12/14     12/14    2.95     0
    gemma3:4b           3.3GB     11/14     14/14    4.29     1

qwen2.5:7b is the near miss worth naming: fastest of the four, no false
blocks, and the ONLY model that fails grounding. 12/14 means it produced
evidence quotes that do not occur in the headline. This whole pipeline is
built on `occurs_verbatim` so the model cannot invent its evidence, so a model
that fabricates quotes is disqualified however fast it is.

gemma3:4b falls to 11/14 and starts failing the bystander case — reading a
Broadcom/VMware deal as AMD doing the acquiring.

Nothing smaller is safe. Memory pressure on this box has come from loading a
SECOND model beside this one, never from this one.

Loading is serialised with keep_alive=0 so two models never share the GPU.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import datetime, timezone

import httpx

from scanner.llm.grounding import occurs_verbatim
from scanner.llm.ollama import LLMError, chat_json
from scanner.models import NewsItem

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

#: ticker, company, headline, expected deal_role, tier, what it probes.
#:
#: The two that actually gate trades are a takeover TARGET, which must never be
#: bought, and litigation, which makes price structure unreliable. Distractors
#: matter as much as the positives — a model that flags everything is useless
#: in a different way.
CASES: tuple[tuple[str, str, str, str, str, str], ...] = (
    # --- easy: a floor, not a ranking ------------------------------------
    ("AMD", "Advanced Micro Devices",
     "AMD announces agreement to acquire ZT Systems for $4.9 billion",
     "acquirer", "easy", "plain acquirer"),
    ("SUPN", "Supernus Pharmaceuticals",
     "Supernus Pharmaceuticals to be acquired by Sage Therapeutics in $8.6 billion deal",
     "target", "easy", "plain target — the case that created the news gate"),
    ("JNJ", "Johnson & Johnson",
     "Johnson & Johnson faces $2.1 billion verdict in talc litigation appeal",
     "none", "easy", "litigation is not a deal"),
    ("KO", "Coca-Cola",
     "Coca-Cola declares quarterly dividend of $0.53 per share",
     "none", "easy", "dividend distractor"),
    ("NVDA", "NVIDIA",
     "NVIDIA shares rise as analysts raise price targets ahead of earnings",
     "none", "easy", "analyst note distractor"),
    ("MO", "Altria",
     "Altria Group completes acquisition of NJOY Holdings",
     "acquirer", "easy", "completed acquisition"),

    # --- hard: where this classification actually fails -------------------
    ("AMD", "Advanced Micro Devices",
     "AMD shares climb after Broadcom agrees to acquire VMware for $61 billion",
     "none", "hard", "deal is between OTHER companies; AMD is a bystander"),
    ("SUPN", "Supernus Pharmaceuticals",
     "Supernus Pharmaceuticals completes acquisition of Adamas Pharmaceuticals",
     "acquirer", "hard", "same name as the famous target case, opposite role"),
    ("IP", "International Paper",
     "International Paper to sell its global cellulose fibers business to Brookfield",
     "none", "hard", "a unit is sold, not the company"),
    ("PFE", "Pfizer",
     "Pfizer and Seagen call off merger talks after regulatory pushback",
     "none", "hard", "the deal is dead; price returns to its own chart"),
    ("CEG", "Constellation Energy",
     "Shares of Constellation Energy surge on report it is in advanced talks "
     "to be bought by NextEra",
     "target", "hard", "target role arrives late, after the price move"),
    ("KO", "Coca-Cola",
     "Elliott Management builds $2 billion stake in Coca-Cola",
     "none", "hard", "a stake is not a bid"),
    ("MO", "Altria",
     "Acquisition of NJOY Holdings by Altria Group cleared by FTC",
     "acquirer", "hard", "passive voice puts the acquirer last"),
    ("JNJ", "Johnson & Johnson",
     "Johnson & Johnson unit files for bankruptcy to resolve talc claims",
     "none", "hard", "structural news, but not a deal"),
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "deal_role": {"type": "string",
                      "enum": ["acquirer", "target", "none"]},
        "evidence_quote": {"type": "string"},
    },
    "required": ["deal_role", "evidence_quote"],
}

_PROMPT = """Headline about {company} ({ticker}):

{title}

deal_role: is {ticker} being ACQUIRED (target), doing the acquiring (acquirer), \
or is this not a deal at all (none)? Litigation, dividends and analyst notes are \
"none".

evidence_quote: copy a span VERBATIM from the headline supporting your call, or \
an empty string if there is no deal."""


def _is_grounded(expected: str, got: str, quote: str, item: NewsItem) -> bool:
    """An empty quote is only honest when there was nothing to quote.

    Scoring any empty quote as grounded rewards a model for saying nothing,
    and every model then scores full marks on a metric that measured silence.
    """
    if not quote.strip():
        return got == "none"
    return occurs_verbatim(quote, item)


async def bench(
    model: str, client: httpx.AsyncClient, cases: tuple, verbose: bool,
) -> dict:
    correct = grounded = failed = 0
    misses: list[str] = []
    started = time.perf_counter()
    for ticker, company, title, expected, tier, probe in cases:
        item = NewsItem(id=title[:40], title=title, summary="", url="",
                        source="bench", queried_ticker=ticker,
                        fetched_at=datetime.now(timezone.utc))
        try:
            raw = await chat_json(
                _PROMPT.format(ticker=ticker, company=company, title=title),
                _SCHEMA, client, model=model, keep_alive="0",
            )
        except (LLMError, ValueError):
            failed += 1
            misses.append(f"{ticker} ({probe}): call failed")
            continue
        got = str(raw.get("deal_role", "")).lower()
        quote = str(raw.get("evidence_quote", ""))
        hit = got == expected
        correct += hit
        grounded += _is_grounded(expected, got, quote, item)
        if not hit:
            misses.append(f"{ticker} [{tier}] {probe}: "
                          f"expected {expected}, got {got or '-'}")
        if verbose or not hit:
            mark = f"{GREEN}ok  {RESET}" if hit else f"{RED}MISS{RESET}"
            print(f"    {mark} {ticker:<5} {tier:<5} expected {expected:<9} "
                  f"got {got or '-':<9} {DIM}{quote[:36]}{RESET}")
    elapsed = time.perf_counter() - started
    return {"correct": correct, "grounded": grounded, "failed": failed,
            "misses": tuple(misses), "per_call": elapsed / max(1, len(cases))}


async def main(models: tuple[str, ...], tier: str, verbose: bool) -> None:
    cases = tuple(c for c in CASES if tier == "all" or c[4] == tier)
    if not cases:
        print(f"{RED}no cases in tier {tier}{RESET}")
        return
    hard = sum(1 for c in cases if c[4] == "hard")
    print(f"{DIM}{len(cases)} cases ({hard} hard), {len(models)} model(s){RESET}")

    async with httpx.AsyncClient(timeout=300) as client:
        results = {}
        for model in models:
            print(f"\n{BOLD}{model}{RESET}")
            results[model] = await bench(model, client, cases, verbose)

    n = len(cases)
    print(f"\n{BOLD}{'model':<26}{'correct':>9}{'grounded':>10}{'failed':>8}"
          f"{'s/call':>9}{RESET}")
    for model, r in results.items():
        colour = GREEN if r["correct"] == n else (
            YELLOW if r["correct"] >= n - 1 else RED)
        print(f"  {model:<24}{colour}{int(r['correct']):>7}/{n}{RESET}"
              f"{int(r['grounded']):>8}/{n}{int(r['failed']):>8}"
              f"{r['per_call']:>9.2f}")

    for model, r in results.items():
        if r["misses"]:
            print(f"\n{BOLD}{model} missed{RESET}")
            for miss in r["misses"]:
                print(f"  {RED}{miss}{RESET}")

    spread = {r["correct"] for r in results.values()}
    if len(models) > 1 and len(spread) == 1:
        print(f"\n{YELLOW}Every model scored the same. This benchmark cannot "
              f"rank them — it is not evidence for a model change.{RESET}")
    else:
        print(f"\n{DIM}A larger model that does not score higher is buying "
              f"nothing here.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--models", nargs="+",
                   default=["gemma3:12b", "qwen2.5:14b-instruct", "llama3.1:8b"])
    p.add_argument("--tier", choices=["all", "easy", "hard"], default="all")
    p.add_argument("--verbose", action="store_true",
                   help="print every case, not only the misses")
    a = p.parse_args()
    asyncio.run(main(tuple(a.models), a.tier, a.verbose))
