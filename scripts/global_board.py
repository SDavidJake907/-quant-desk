"""Global market board:  python scripts/global_board.py

Shows every tracked index by region with its move, plus whether that market was
OPEN or CLOSED when the quote was taken. The status matters: a closed market's
"move" is a finished number, while an open market's is still changing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from scanner.sources.indices import IndexQuote, fetch_all
from scanner.universe import INDICES, REGION_ORDER

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
GREEN, RED, GREY = "\033[32m", "\033[31m", "\033[90m"


def _colour(pct: float) -> str:
    if pct > 0:
        return GREEN
    if pct < 0:
        return RED
    return GREY


def _arrow(pct: float) -> str:
    if pct > 0:
        return "▲"
    if pct < 0:
        return "▼"
    return "="


def _row(q: IndexQuote) -> str:
    colour = _colour(q.change_pct)
    status = f"{GREEN}OPEN{RESET}" if q.is_open else f"{GREY}closed{RESET}"
    return (
        f"  {q.name:<20.20} {q.country:<15.15} "
        f"{q.price:>12,.2f} "
        f"{colour}{_arrow(q.change_pct)} {q.change:>+10,.2f}  {q.change_pct:>+6.2f}%{RESET}  "
        f"{status:<16} {DIM}{q.local_time:%H:%M %Z}{RESET}"
    )


def _summarise(quotes: tuple[IndexQuote, ...]) -> str:
    """Breadth: how much of the tracked world is up versus down."""
    if not quotes:
        return "no data"
    up = sum(1 for q in quotes if q.change_pct > 0)
    down = sum(1 for q in quotes if q.change_pct < 0)
    avg = sum(q.change_pct for q in quotes) / len(quotes)
    return f"{GREEN}{up} up{RESET} / {RED}{down} down{RESET}   avg {_colour(avg)}{avg:+.2f}%{RESET}"


async def main() -> None:
    async with httpx.AsyncClient() as client:
        quotes, errors = await fetch_all(INDICES, client)

    now = datetime.now(timezone.utc)
    print(f"\n{BOLD}GLOBAL MARKET BOARD{RESET}   {now:%Y-%m-%d %H:%M} UTC")
    print(f"{DIM}moves are vs each market's own previous close, in local currency{RESET}")

    for region in REGION_ORDER:
        in_region = tuple(q for q in quotes if q.region == region)
        if not in_region:
            continue
        print(f"\n{BOLD}{region}{RESET}   {_summarise(in_region)}")
        print(
            f"  {DIM}{'Index':<20} {'Country':<15} {'Level':>12} "
            f"{'Change':>12} {'%':>8}   {'Status':<8} Local{RESET}"
        )
        for quote in sorted(in_region, key=lambda q: q.change_pct, reverse=True):
            print(_row(quote))

    if quotes:
        print(f"\n{BOLD}World{RESET}   {_summarise(quotes)}")
        best = max(quotes, key=lambda q: q.change_pct)
        worst = min(quotes, key=lambda q: q.change_pct)
        print(f"  best  {GREEN}{best.name} {best.change_pct:+.2f}%{RESET}")
        print(f"  worst {RED}{worst.name} {worst.change_pct:+.2f}%{RESET}")

    if errors:
        print(f"\n{DIM}unavailable ({len(errors)}):{RESET}")
        for error in errors:
            print(f"  {DIM}- {error}{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
