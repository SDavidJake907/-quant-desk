"""Does a bigger model actually do this job better?

    python scripts/bench_llm.py                       # models already pulled
    python scripts/bench_llm.py --models gemma3:12b mistral-small:24b

The two jobs this project gives a model are grammar-constrained classification:
read headlines, emit an enum and a verbatim quote. That is not the kind of task
where scale usually pays — the schema already forbids the failure modes a
larger model would be better at avoiding.

So this measures it rather than assuming, on cases with known right answers,
and reports the two things that matter: does it get the call right, and does it
quote text that actually exists. A model that scores the same as a smaller one
is a model whose extra VRAM buys nothing here.

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

#: Headlines with unambiguous right answers, including the two that actually
#: gate trades: a takeover TARGET must never be bought, and litigation makes
#: price structure unreliable. The distractors matter as much as the positives —
#: a model that flags everything is useless in a different way.
CASES: tuple[tuple[str, str, str, str], ...] = (
    ("AMD", "Advanced Micro Devices",
     "AMD announces agreement to acquire ZT Systems for $4.9 billion",
     "acquirer"),
    ("SUPN", "Supernus Pharmaceuticals",
     "Supernus Pharmaceuticals to be acquired by Sage Therapeutics in $8.6 billion deal",
     "target"),
    ("JNJ", "Johnson & Johnson",
     "Johnson & Johnson faces $2.1 billion verdict in talc litigation appeal",
     "none"),
    ("KO", "Coca-Cola",
     "Coca-Cola declares quarterly dividend of $0.53 per share",
     "none"),
    ("NVDA", "NVIDIA",
     "NVIDIA shares rise as analysts raise price targets ahead of earnings",
     "none"),
    ("MO", "Altria",
     "Altria Group completes acquisition of NJOY Holdings",
     "acquirer"),
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


async def bench(model: str, client: httpx.AsyncClient) -> dict[str, float]:
    correct = grounded = failed = 0
    started = time.perf_counter()
    for ticker, company, title, expected in CASES:
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
            continue
        got = str(raw.get("deal_role", "")).lower()
        quote = str(raw.get("evidence_quote", ""))
        hit = got == expected
        correct += hit
        grounded += (not quote.strip()) or occurs_verbatim(quote, item)
        mark = f"{GREEN}ok {RESET}" if hit else f"{RED}MISS{RESET}"
        print(f"    {mark} {ticker:<5} expected {expected:<9} got {got or '-':<9} "
              f"{DIM}{quote[:44]}{RESET}")
    elapsed = time.perf_counter() - started
    return {"correct": correct, "grounded": grounded, "failed": failed,
            "seconds": elapsed, "per_call": elapsed / max(1, len(CASES))}


async def main(models: tuple[str, ...]) -> None:
    async with httpx.AsyncClient(timeout=300) as client:
        results = {}
        for model in models:
            print(f"\n{BOLD}{model}{RESET}")
            results[model] = await bench(model, client)

    n = len(CASES)
    print(f"\n{BOLD}{'model':<26}{'correct':>9}{'grounded':>10}{'failed':>8}"
          f"{'s/call':>9}{RESET}")
    for model, r in results.items():
        colour = GREEN if r["correct"] == n else (YELLOW if r["correct"] >= n - 1 else RED)
        print(f"  {model:<24}{colour}{int(r['correct']):>7}/{n}{RESET}"
              f"{int(r['grounded']):>8}/{n}{int(r['failed']):>8}"
              f"{r['per_call']:>9.2f}")
    print(f"\n{DIM}A larger model that does not score higher is buying nothing "
          f"here.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--models", nargs="+",
                   default=["gemma3:12b", "qwen2.5:14b-instruct", "llama3.1:8b"])
    a = p.parse_args()
    asyncio.run(main(tuple(a.models)))
