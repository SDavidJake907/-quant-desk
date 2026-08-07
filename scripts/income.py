"""The income sleeve: dividend payers ranked by record, not by yield.

    python scripts/income.py                     # S&P 500, ranked
    python scripts/income.py --index all --top 30
    python scripts/income.py --min-yield 3.0 --min-streak 5

WHAT THIS DOES AND DOES NOT DO
It ranks. It does not buy, and there is no flag that makes it buy — the income
sleeve is a decision to hold a company for years, which is not a decision to
delegate to a screen that has read four years of payment history.

THE RANKING
Yield alone selects for distress: a yield rises when the price collapses, so
the highest numbers on any yield screen are disproportionately companies in
trouble. AT&T shows 4.7% because it cut its dividend and the price fell.

So the score pairs yield with the record behind it — consecutive years of
raises, growth rate, and a hard rejection for any cut in the window. A 6% yield
with a cut two years ago ranks below a 3% yield raised every year, because the
second one is far more likely to still be paying in a decade.

SECTOR IS SHOWN BECAUSE CONCENTRATION IS THE REAL RISK HERE
High-yield screens converge on utilities, telecoms, energy and tobacco. A
sleeve built from the top ten names of an unconstrained yield screen is a bet
on two sectors wearing the costume of a diversified income portfolio, and it
will fall together on a single rate decision. The board reports sector counts
so that is visible before it is bought, not after.
"""

from __future__ import annotations

import argparse
import asyncio
import collections

import httpx

from scanner.sources import composite
from scanner.sources.dividend_history import DividendHistory, fetch
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}


def score(history: DividendHistory) -> float:
    """Yield, weighted by the record behind it.

    Capped deliberately: yield above 8% is far more often a broken business
    than a generous one, so points stop accruing there rather than rewarding
    the tail this screen exists to avoid.
    """
    if history.was_cut():
        return 0.0
    points = min(history.yield_pct(), 8.0) * 10.0
    points += min(history.growth_streak(), 10) * 6.0
    points += min(max(history.growth_pct(), 0.0), 12.0) * 2.0
    return round(points, 1)


def _eligible(history: DividendHistory, min_yield: float, min_streak: int) -> bool:
    return (
        history.pays_dividends
        and not history.was_cut()
        and history.yield_pct() >= min_yield
        and history.growth_streak() >= min_streak
        and history.frequency() != "irregular"
    )


async def main(index: str, top: int, min_yield: float, min_streak: int) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        universe = await composite.load_universe(client, INDEXES[index])
        sector_of = {m.symbol: m.sector for m in universe}
        symbols = [m.symbol for m in universe]
        print(f"{DIM}reading dividend history for {len(symbols)} names{RESET}")

        histories: list[DividendHistory] = []
        for start in range(0, len(symbols), 50):        # polite batching
            chunk = symbols[start:start + 50]
            got = await asyncio.gather(*(fetch(s, client) for s in chunk),
                                       return_exceptions=True)
            histories += [h for h in got
                          if isinstance(h, DividendHistory) and h.pays_dividends]

    cut = [h for h in histories if h.was_cut()]
    passing = [h for h in histories if _eligible(h, min_yield, min_streak)]
    passing.sort(key=score, reverse=True)

    print(f"\n{DIM}{len(histories)} pay a dividend; {len(cut)} cut in the window; "
          f"{len(passing)} pass yield >= {min_yield}% and a {min_streak}-year "
          f"raise streak{RESET}\n")

    print(f"  {'sym':<7}{'sector':<24}{'yield':>7}{'streak':>8}{'growth':>8}"
          f"{'freq':>11}{'score':>7}")
    for history in passing[:top]:
        streak = history.growth_streak()
        colour = GREEN if streak >= 5 else (CYAN if streak >= 3 else DIM)
        print(f"  {history.symbol:<7}{sector_of.get(history.symbol,'?')[:23]:<24}"
              f"{history.yield_pct():>6.2f}%{colour}{streak:>7}y{RESET}"
              f"{history.growth_pct():>7.1f}%{history.frequency():>11}"
              f"{score(history):>7.0f}")

    sectors = collections.Counter(sector_of.get(h.symbol, "?") for h in passing[:top])
    print(f"\n  {BOLD}sector spread of the top {min(top, len(passing))}{RESET}")
    for sector, n in sectors.most_common():
        bar = "#" * n
        warn = f"  {YELLOW}<-- concentrated{RESET}" if n > max(3, top // 4) else ""
        print(f"    {sector[:26]:<27}{n:>3}  {bar}{warn}")

    if cut:
        worst = sorted(cut, key=lambda h: -h.yield_pct())[:5]
        print(f"\n  {RED}rejected for cutting{RESET} {DIM}(high yield is often the "
              f"symptom, not the offer){RESET}")
        for history in worst:
            print(f"    {history.symbol:<7}{history.yield_pct():>6.2f}%  "
                  f"{DIM}{', '.join(f'{y}:{v:.2f}' for y, v in sorted(history.complete_years().items()))}{RESET}")

    print(f"\n{YELLOW}This screen ranks. It never buys.{RESET}")
    print(f"{DIM}Holding a company for years is not a decision to delegate to "
          f"four years of payment history.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--min-yield", type=float, default=2.0)
    p.add_argument("--min-streak", type=int, default=2,
                   help="consecutive complete years of a higher payout")
    a = p.parse_args()
    asyncio.run(main(a.index, a.top, a.min_yield, a.min_streak))
