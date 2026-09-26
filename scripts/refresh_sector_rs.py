"""Write today's median sector relative strength, once, off the hot path.

    python scripts/refresh_sector_rs.py
    python scripts/refresh_sector_rs.py --index all

Runs from `scheduled_data.bat` after the bar collection, which has already
warmed the daily cache this reads, so it costs little there and saves 4.45s on
every scan that finds a candidate — 14.42s on the first scan of the day, the
one closest to the open.

ADVISORY ONLY. These medians are context for the ranking prompt and gate
nothing. A failure here degrades a prompt; it must never stop a scan, and
`sector_strength.today()` falls back to computing them itself if this has not
run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.features import sector_strength
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED = "\033[32m", "\033[31m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}


async def main(index: str) -> int:
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])
        medians = await sector_strength.refresh(client, universe)
    if not medians:
        print(f"{RED}no sector medians computed — the scan will fall back to "
              f"computing them itself{RESET}")
        return 1
    print(f"{GREEN}{len(medians)}{RESET} sector medians written to "
          f"{sector_strength.PATH}")
    for sector, value in sorted(medians.items(), key=lambda kv: -kv[1]):
        print(f"  {sector:<26}{value:>+7.2f}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.index)))
