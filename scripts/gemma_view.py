"""What the model actually sees, through the production ranking path.

    python scripts/gemma_view.py                       # a liquid default set
    python scripts/gemma_view.py --symbols PFE KO AMD
    python scripts/gemma_view.py --setups              # today's real setups

READ-ONLY, and outside the frozen trading path. It fetches headlines, asks the
model, and prints. It places no order and writes no audit record.

WHY IT EXISTS
The model's read reaches the terminal only when a scan produces candidates,
and the freshness gate currently admits almost nothing — the last scans showed
79 setups and 0 candidates. So the one component asked for a judgment on every
decision is also the one nobody can watch. This makes it watchable on demand.

IT USES THE REAL PATH
`ranking.rank()`, the same call `trade.py` makes, with headlines from the same
`yahoo_rss` source. Nothing here re-implements the prompt, so what prints is
what the scanner would have been told.

READ THE GROUNDING COLUMN
An ungrounded opinion is one the model could not evidence with a verbatim
quote, and `order()` demotes it to neutral rather than letting it move a
candidate. A run full of ungrounded convictions is a run whose ordering means
nothing, and that is worth seeing directly.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

import httpx

# Headlines carry typographic characters — non-breaking hyphens, curly quotes,
# em dashes — and the Windows console is cp1252. Printing a verbatim quote
# therefore crashed this script on a real Yahoo headline. Replace rather than
# raise: an unprintable character in a quote is a display problem, and killing
# the run over one loses every opinion already computed.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.llm import ranking
from scanner.sources import yahoo_rss

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

#: Liquid names across sectors, so a default run exercises real variety.
DEFAULT = ("AAPL", "MSFT", "NVDA", "PFE", "KO", "XOM", "JPM", "CAT")

CONVICTION_COLOUR = {"strong": GREEN, "neutral": DIM, "weak": YELLOW}


async def main(symbols: tuple[str, ...], show_quotes: bool) -> int:
    print(f"{DIM}model {settings.triage_model} · {len(symbols)} symbol(s) · "
          f"production ranking path{RESET}\n")

    async with httpx.AsyncClient() as client:
        headlines: dict[str, tuple] = {}
        for symbol in symbols:
            try:
                headlines[symbol] = await yahoo_rss.fetch(symbol, client)
            except Exception as exc:                      # noqa: BLE001
                print(f"  {YELLOW}{symbol}: no headlines ({exc}){RESET}")
                headlines[symbol] = ()

        counts = {s: len(v) for s, v in headlines.items()}
        print(f"{DIM}headlines: "
              f"{', '.join(f'{s} {n}' for s, n in counts.items())}{RESET}\n")

        started = time.perf_counter()
        opinions = await ranking.rank(headlines, client)
        elapsed = time.perf_counter() - started

    if not opinions:
        print(f"{RED}no opinions returned — is Ollama up?{RESET}")
        return 1

    by_symbol = {o.symbol: o for o in opinions}
    ordering = ranking.order(opinions)

    print(f"{BOLD}{'rank':<6}{'symbol':<8}{'conviction':<12}{'driver':<10}"
          f"{'grounded':<10}concern{RESET}")
    for position, symbol in enumerate(ordering, start=1):
        o = by_symbol[symbol]
        colour = CONVICTION_COLOUR.get(o.conviction, DIM)
        ground = f"{GREEN}yes{RESET}" if o.grounded else f"{RED}NO{RESET} "
        concern = o.concern if o.concern and o.concern != "none" else ""
        print(f"{position:<6}{symbol:<8}{colour}{o.conviction:<12}{RESET}"
              f"{o.driver:<10}{ground:<19}{DIM}{concern[:38]}{RESET}")
        if show_quotes and o.evidence_quote:
            print(f"      {DIM}“{o.evidence_quote[:96]}”{RESET}")

    ungrounded = sum(1 for o in opinions if not o.grounded)
    weak = sum(1 for o in opinions if o.conviction == "weak")
    print(f"\n{DIM}{len(opinions)} opinion(s) in {elapsed:.1f}s "
          f"({elapsed / max(1, len(opinions)):.1f}s each) · "
          f"{weak} weak · {ungrounded} ungrounded{RESET}")
    if ungrounded:
        print(f"{YELLOW}{ungrounded} opinion(s) could not be evidenced and "
              f"were demoted to neutral for ordering.{RESET}")
    print(f"{DIM}Advisory only. This ordering moves nothing today — "
          f"`llm_rank` is recorded beside the deterministic rank and acted on "
          f"by neither.{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="+", default=list(DEFAULT))
    p.add_argument("--quotes", action="store_true",
                   help="print the verbatim evidence for each call")
    a = p.parse_args()
    raise SystemExit(asyncio.run(
        main(tuple(s.upper() for s in a.symbols), a.quotes)))
