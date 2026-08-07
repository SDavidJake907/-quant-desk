"""The universe by sector — money flow, leaders, and where setups are firing.

    python scripts/sectors.py                  # all 11 sectors
    python scripts/sectors.py --sector Energy  # one sector in full
    python scripts/sectors.py --setups         # only sectors with live setups

Sector is the one grouping every name already carries: GICS classification
comes with the index membership tables, so all 1,504 names have it for free.

WHAT THIS IS FOR, AND THE LINE IT DOES NOT CROSS
Sector rotation is real and observable — money leaves energy and arrives in
technology, and it does so over weeks. This board shows that. What it does NOT
do is filter trades by it.

That restraint is deliberate. Nine context gates were tested against this
strategy and seven made it worse; the ones that felt most obviously right —
"only buy above the 200 EMA" — were the worst of all. A sector filter is the
same shape of idea: plausible, untested, and one more slice of an 86-day
window. So sector is recorded on every setup and analysed later, on data
nobody chose it from.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics

import httpx

from scanner.features import technicals
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import fetch_intraday, max_range
from scanner.heatmap import SECTOR_ORDER
from scanner.screens.smc import scan
from scanner.screens.trend import evaluate
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500

BENCHMARK = "^GSPC"
BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"


def _colour(value: float) -> str:
    return GREEN if value > 0 else (RED if value < 0 else DIM)


async def main(only: str | None, setups_only: bool, top: int, minutes: int) -> None:
    async with httpx.AsyncClient() as client:
        universe = await composite.load_universe(client, COMPOSITE_1500)
        symbols = tuple(m.symbol for m in universe)
        sector_of = {m.symbol: m.sector for m in universe}

        benchmark = technicals.compute(await fetch_bars(BENCHMARK, client, range_="2y"))
        daily, _ = await fetch_many(symbols, client, range_="2y")

        print(f"{DIM}benchmark 12-1 momentum {benchmark.momentum_12_1:+.1f}%{RESET}")

        # Live setups, so the board shows where the screen is actually firing.
        live: dict[str, list[str]] = {}
        if setups_only or only:
            wanted = [s for s in symbols if not only or sector_of.get(s) == only]
            frames = await asyncio.gather(
                *(fetch_intraday(s, client, minutes=minutes, range_=max_range(minutes))
                  for s in wanted),
                return_exceptions=True,
            )
            for symbol, bars in zip(wanted, frames):
                if isinstance(bars, Exception):
                    continue
                found = scan(bars, k=2, require_value_sweep=True,
                             min_risk_pct=1.5, min_reward_risk=1.5)
                if any(len(bars) - 1 - s.trigger_index < 8 for s in found):
                    live.setdefault(sector_of.get(symbol, "?"), []).append(symbol)

    # --- per-name features ------------------------------------------------
    rows: dict[str, list[tuple[str, float, bool]]] = {}
    for member in universe:
        series = daily.get(member.symbol)
        if series is None:
            continue
        features = technicals.compute(series)
        rs = technicals.relative_strength(features, benchmark)
        if rs is None:
            continue
        candidate = evaluate(features, rs)
        rows.setdefault(member.sector, []).append(
            (member.symbol, rs, candidate.passed_all)
        )

    ordered = [s for s in SECTOR_ORDER if s in rows]
    ordered += sorted(s for s in rows if s not in SECTOR_ORDER)
    if only:
        ordered = [s for s in ordered if s == only]
        if not ordered:
            raise SystemExit(f"no sector named {only!r}. Try: {', '.join(rows)}")

    # Rank sectors by median relative strength — leaders to laggards.
    ranked = sorted(
        ordered, key=lambda s: statistics.median(r[1] for r in rows[s]), reverse=True
    )

    print(f"\n{BOLD}SECTORS{RESET} {DIM}ranked by median relative strength "
          f"vs the S&P 500{RESET}\n")
    print(f"  {'sector':<24} {'names':>6} {'median RS':>11} {'beating':>9} "
          f"{'passing':>8} {'setups':>7}")
    for sector in ranked:
        names = rows[sector]
        median = statistics.median(r[1] for r in names)
        beating = sum(1 for r in names if r[1] > 0)
        passing = sum(1 for r in names if r[2])
        firing = len(live.get(sector, []))
        print(f"  {sector:<24} {len(names):>6} "
              f"{_colour(median)}{median:>+10.1f}pp{RESET} "
              f"{beating:>4}/{len(names):<4} {passing:>8} "
              f"{(str(firing) if firing else '-'):>7}")

    if only or top:
        for sector in ranked:
            names = sorted(rows[sector], key=lambda r: -r[1])[:top]
            if not names:
                continue
            print(f"\n  {BOLD}{sector}{RESET}")
            for symbol, rs, passed in names:
                mark = f" {GREEN}*{RESET}" if passed else "  "
                firing = f"  {CYAN}setup{RESET}" if symbol in live.get(sector, []) else ""
                print(f"    {symbol:<6} {_colour(rs)}{rs:>+8.1f}pp{RESET}{mark}{firing}")

    print(f"\n{DIM}* passes all six trend criteria.{RESET}")
    print(f"{YELLOW}Sector is shown, never filtered on.{RESET}")
    print(f"{DIM}Seven of nine context gates tested against this strategy made it{RESET}")
    print(f"{DIM}worse, and the most plausible-sounding were the worst. Sector is{RESET}")
    print(f"{DIM}recorded on every setup and judged later on fresh data.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--sector", default=None, help="e.g. Energy, Financials")
    p.add_argument("--setups", action="store_true", dest="setups_only",
                   help="also scan for live setups (slower)")
    p.add_argument("--top", type=int, default=0, help="top N names per sector")
    p.add_argument("--minutes", type=int, default=30)
    a = p.parse_args()
    asyncio.run(main(a.sector, a.setups_only, a.top, a.minutes))
