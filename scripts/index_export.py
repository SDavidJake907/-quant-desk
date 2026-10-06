"""Export S&P 500 index daily OHLCV (with volume) for the GPU research interpreter.

    python scripts/index_export.py

Writes data/factors/index_GSPC.pkl: dates (ISO), open/high/low/close/volume lists,
30 years of Yahoo daily ^GSPC. Read-only; used by research_gpu/vcp_ftd_test.py
(follow-through days and distribution days need the index's volume).
"""

from __future__ import annotations

import asyncio
import pickle
import sys
from pathlib import Path

import httpx

from scanner.features.bars import fetch_bars

OUT = Path("data/factors/index_GSPC.pkl")


async def main() -> int:
    async with httpx.AsyncClient(timeout=60) as client:
        b = await fetch_bars("^GSPC", client, range_="30y")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(pickle.dumps({"dates": [d.isoformat() for d in b.dates], "open": list(b.opens),
                                  "high": list(b.highs), "low": list(b.lows), "close": list(b.closes),
                                  "volume": list(b.volumes)}))
    print(f"^GSPC {b.dates[0]}..{b.dates[-1]}, {len(b.dates)} days, "
          f"{sum(v > 0 for v in b.volumes)} with volume -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
