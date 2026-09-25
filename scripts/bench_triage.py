"""The benchmark cases, through the REAL triage prompt.

    python scripts/bench_triage.py
    python scripts/bench_triage.py --tier hard

WHY THIS IS SEPARATE FROM bench_llm.py
`bench_llm.py` asks the model a purpose-built question with its own small
prompt. That measures the MODEL. It does not measure what this system actually
sends, which is `llm/triage.py`'s prompt — a longer instruction carrying role
classification, materiality, category and grounding alongside `deal_role`.

A model can score well on the short prompt and badly on the real one, and it
is the real one that decides which names the news gate blocks. So this runs
the identical cases through `triage()` itself: same prompt, same schema, same
batching, same code path a live scan takes.

WHAT IT REPORTS
`deal_role` accuracy, because that is the field the gate acts on — only
`target` blocks a trade. It also reports the ROLE the model assigned, since a
headline about someone else should come back `example` or `unrelated` rather
than `subject`, and that is the real defence against the bystander case.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import datetime, timezone

import httpx

from scanner.llm.triage import triage
from scanner.models import NewsItem

from bench_llm import CASES

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[32m", "\033[36m"
RESET = "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"


async def run(cases, client: httpx.AsyncClient) -> dict:
    correct = grounded = missing = 0
    misses: list[str] = []
    started = time.perf_counter()

    for ticker, company, title, expected, tier, probe in cases:
        item = NewsItem(
            id=title[:40], source="bench", queried_ticker=ticker,
            title=title, summary="", url="",
            fetched_at=datetime.now(timezone.utc),
        )
        results = await triage(ticker, company, (item,), client)
        if not results:
            missing += 1
            misses.append(f"{ticker} [{tier}] {probe}: no verdict returned")
            print(f"    {RED}NONE{RESET} {ticker:<5} {tier:<5} "
                  f"{DIM}triage returned nothing{RESET}")
            continue

        verdict = results[0].verdict
        got = str(getattr(verdict, "deal_role", "")).lower()
        role = str(getattr(verdict, "role", ""))
        quote = str(getattr(verdict, "evidence_quote", "") or "")
        hit = got == expected
        correct += hit
        grounded += bool(quote.strip())
        if not hit:
            misses.append(f"{ticker} [{tier}] {probe}: expected {expected}, "
                          f"got {got or '-'} (role={role})")
        mark = f"{GREEN}ok  {RESET}" if hit else f"{RED}MISS{RESET}"
        print(f"    {mark} {ticker:<5} {tier:<5} expected {expected:<9} "
              f"got {got or '-':<9} {DIM}role={role:<11}{RESET}")

    return {"correct": correct, "grounded": grounded, "missing": missing,
            "misses": tuple(misses), "n": len(cases),
            "seconds": time.perf_counter() - started}


async def main(tier: str) -> int:
    cases = tuple(c for c in CASES if tier == "all" or c[4] == tier)
    hard = sum(1 for c in cases if c[4] == "hard")
    print(f"{DIM}{len(cases)} cases ({hard} hard) through the PRODUCTION "
          f"triage prompt{RESET}\n")

    async with httpx.AsyncClient(timeout=300) as client:
        result = await run(cases, client)

    n = result["n"]
    colour = GREEN if result["correct"] == n else (
        YELLOW if result["correct"] >= n - 2 else RED)
    print(f"\n{BOLD}deal_role{RESET}  {colour}{result['correct']}/{n}{RESET}"
          f"   {DIM}grounded {result['grounded']}/{n} · "
          f"no verdict {result['missing']} · "
          f"{result['seconds'] / max(1, n):.2f}s per case{RESET}")

    if result["misses"]:
        print(f"\n{BOLD}missed{RESET}")
        for miss in result["misses"]:
            print(f"  {RED}{miss}{RESET}")

    print(f"\n{DIM}Only `target` blocks a trade. A wrong `acquirer` costs "
          f"nothing operationally; a wrong `target` refuses a real setup.{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tier", choices=["all", "easy", "hard"], default="all")
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.tier)))
