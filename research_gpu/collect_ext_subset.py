"""Collect pre/regular/after-hours 30m SIP candles (04:00-20:00 ET) from 2024-09-01 for the
names TRIALS #97/#98 need -- the full S&P 500 took hours while sharing Alpaca's rate limit.

Names (fixed before the test runs): the 20 largest (signal_lab.BIGCAP) + every name that
was one of the 30 monthly fast movers since 2024-09 (15m store) + 60 other S&P 500 names
drawn with a fixed seed (the "ALL" comparison sample). Writes data/intraday_ext_2024/.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import collect_alpaca  # noqa: E402
import signal_lab as lab  # noqa: E402

STORE = ROOT / "data/intraday_ext_2024"


def names() -> list[str]:
    s15 = {p.name.split("__")[0]: lab.load_15m(p) for p in sorted((ROOT / "data/intraday_alpaca").glob("*__15m__session.json"))}
    fast = lab.fast_months({s: lab.daily(b) for s, b in s15.items()}, 30)
    fast_names = set().union(*(v for (y, m), v in fast.items() if (y, m) >= (2024, 9)))
    chosen = set(lab.BIGCAP) | fast_names
    rest = sorted(set(s15) - chosen)
    chosen |= set(random.Random(97).sample(rest, 60))
    return sorted(chosen)


async def main() -> int:
    todo = [s for s in names() if not (STORE / f"{s}__30m__session.json").exists()]
    STORE.mkdir(parents=True, exist_ok=True)
    print(f"{len(todo)} names to fetch", flush=True)
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=120) as client:
        async def one(s):
            async with sem:
                rows = await collect_alpaca.fetch_symbol(client, s, "2024-09-01", 30, True)
                if isinstance(rows, str):
                    print(f"  {s}: {rows}", flush=True)
                    return
                (STORE / f"{s}__30m__session.json").write_text(json.dumps(rows))
                print(f"  {s}: {len(rows)}", flush=True)
        await asyncio.gather(*(one(s) for s in todo))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
