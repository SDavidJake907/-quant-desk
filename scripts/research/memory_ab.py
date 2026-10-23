"""Does showing the model its own history change what it thinks?

    python scripts/memory_ab.py
    python scripts/memory_ab.py --symbols PSX ACN APP CCL NVDA

READ-ONLY research. It asks the model the same question twice and prints both
answers. It changes no prompt, no policy and no code that trades.

WHY THIS RUNS BEFORE ANYTHING IS WIRED
On 2026-09-24 a retrieval memory was attached to the same model and MADE IT
WORSE: given its own recalled context, qwen2.5:14b went from answering the
Pfizer/Seagen question correctly to answering it wrongly, because the store
ranked the deal ANNOUNCEMENT above the TERMINATION. That was only visible
because the answer was known and both versions were run.

`llm/memory.py` is built differently — from realized R rather than from text —
but "built differently" is a reason to hope, not evidence. The same test
decides it.

WHAT IS COMPARED
`ranking.rank`'s exact prompt and schema, on real current headlines, with and
without the memory block prepended. Anything that differs is the memory's
doing.

WHAT WOULD COUNT AS WORKING
Memory should move convictions for symbols it actually knows about and leave
the rest alone. Two failures to watch for, both of which would disqualify it:

  - it changes verdicts for symbols the memory says nothing about, which is
    the model reacting to the PRESENCE of history rather than its content
  - it turns cautious on a name whose only memory entry is a single loss,
    which is generalising from n=1 — the thing the block explicitly tells it
    not to do
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.llm import memory, ranking
from scanner.llm.ollama import LLMError, chat_json
from scanner.sources import yahoo_rss

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

#: Symbols the memory has something to say about, plus controls it does not.
#: PSX, APP and CCL are losses; ACN and CEG are wins; NVDA and XOM were never
#: traded and are the control — memory must not move them.
DEFAULT = ("PSX", "APP", "CCL", "ACN", "CEG", "NVDA", "XOM")
MEMORY_KNOWS = {"PSX", "APP", "CCL", "ACN", "CEG", "IP", "AMD", "SUPN",
                "EXPE", "Q"}


async def opinions(prompt: str, client: httpx.AsyncClient) -> dict:
    raw = await chat_json(prompt, ranking._SCHEMA, client)
    out = {}
    for row in raw.get("opinions", []):
        symbol = str(row.get("symbol", "")).upper()
        if symbol:
            out[symbol] = (str(row.get("conviction", "")),
                           str(row.get("driver", "")))
    return out


async def main(symbols: tuple[str, ...]) -> int:
    block = memory.build_block(settings.audit_log_path)
    print(f"{BOLD}THE MEMORY BEING SHOWN{RESET}")
    for line in block.splitlines():
        print(f"  {DIM}{line}{RESET}")

    async with httpx.AsyncClient(timeout=300) as client:
        headlines = {}
        for symbol in symbols:
            try:
                headlines[symbol] = await yahoo_rss.fetch(symbol, client)
            except Exception:                            # noqa: BLE001
                headlines[symbol] = ()
        rendered = ranking._render(headlines)
        base = ranking._PROMPT.format(companies=rendered)
        withmem = f"{block}\n\n{base}"

        try:
            # Both arms read the SAME headlines, fetched once above. The
            # first version of this script fetched per run, and Yahoo's feed
            # updates between calls — so the arms were reading different news
            # and every difference looked like a memory effect.
            blind = await opinions(base, client)
            remembered = await opinions(withmem, client)
            # A self-comparison, so the noise floor is on the page rather
            # than assumed. If this is not zero, nothing below means anything.
            repeat = await opinions(base, client)
        except (LLMError, ValueError) as exc:
            print(f"\n{RED}model unreachable: {exc}{RESET}")
            return 1

    noise = sum(1 for s in symbols if blind.get(s) != repeat.get(s))
    tint = GREEN if noise == 0 else RED
    print(f"\n{BOLD}NOISE FLOOR{RESET}  {tint}{noise}/{len(symbols)}{RESET} "
          f"{DIM}symbols differ when NOTHING changed{RESET}")
    if noise:
        print(f"  {RED}the model is not reproducible on this input, so the "
              f"comparison below cannot be trusted{RESET}")

    print(f"\n{BOLD}VERDICTS{RESET}")
    print(f"  {'sym':<6}{'blind':<22}{'with memory':<22}  known?")
    changed_known = changed_unknown = 0
    for symbol in symbols:
        a = blind.get(symbol, ("-", "-"))
        b = remembered.get(symbol, ("-", "-"))
        known = symbol in MEMORY_KNOWS
        moved = a != b
        if moved:
            if known:
                changed_known += 1
            else:
                changed_unknown += 1
        tint = (YELLOW if moved else DIM)
        mark = "yes" if known else f"{CYAN}CONTROL{RESET}"
        print(f"  {symbol:<6}{tint}{a[0] + '/' + a[1]:<22}"
              f"{b[0] + '/' + b[1]:<22}{RESET}  {mark}")

    print(f"\n{BOLD}READING IT{RESET}")
    print(f"  changed, memory knows them   {changed_known}")
    print(f"  changed, memory is silent    {changed_unknown}")
    if changed_unknown:
        print(f"  {RED}the memory moved symbols it says nothing about — "
              f"that is the model reacting to the PRESENCE of history, not "
              f"its content{RESET}")
    elif changed_known:
        print(f"  {YELLOW}only known symbols moved. Check the direction: a "
              f"name whose entire memory is one loss should NOT become "
              f"cautious — the block says not to infer from n=1{RESET}")
    else:
        print(f"  {GREEN}memory changed nothing. Safe, and currently "
              f"pointless — which is the honest state at ten trades{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="+", default=list(DEFAULT))
    a = p.parse_args()
    raise SystemExit(asyncio.run(
        main(tuple(s.upper() for s in a.symbols))))
