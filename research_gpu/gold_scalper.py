"""TRIALS #92: the example scalper on GOLD (GLD, IAU -- gold ETFs, as close to XAUUSD as this account trades).

Operator, 2026-10-22: "lets try the scalping with XAUUSD". Spot XAUUSD is a forex/CFD
product Alpaca does not offer; GLD and IAU are US-listed shares backed by physical gold
that follow it during US hours.

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  data       Alpaca SIP 1-minute candles, regular hours, 2025-10-01 -> the last
             session (scalper_test.candles; cached in data/research_1m)
  names      GLD and IAU, every day
  entry      TRIALS #83 exactly (close crosses above the 20-minute SMA; limit at that
             close, filled only if a candle in the next 2 trades below it; no buys
             from 15:55)
  exits      ORIGINAL (#83 A: +$0.01, no stop, out at 15:55) and #84's 12 dollar
             pairs (target +0.25/0.5/1/2 %, stop -0.5/1/2 % of a $2,000 lot), out at 15:55
  SELECT     the pair with the highest t over days in the first half (to 2026-04-14,
             >= 100 trades); PASS: that pair in the second half, mean > 0 with t >= 3
READ-ONLY research.
"""

from __future__ import annotations

import asyncio
import sys
from itertools import product
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import scalper_dollars as sd  # noqa: E402
import scalper_test as st  # noqa: E402

NAMES = ("GLD", "IAU")


async def main() -> int:
    async with httpx.AsyncClient(timeout=120) as client:
        data = await st.candles(client, NAMES)
    every_day = lambda d: True  # noqa: E731
    rows = []
    for s in NAMES:
        rows += st.trades(data[s], every_day, stop=False)
    a = st.summary([r for r in rows if r[0] < st.SPLIT])
    b = st.summary([r for r in rows if r[0] >= st.SPLIT])
    print(f"\nGOLD (GLD + IAU), original +1c / no stop:")
    print(f"   first half:  {a[0]:5} trades {a[1]:+.4f}% a trade, win {a[2]:.0f}%, worst {a[3]:+.2f}%, ${a[4]:+,.0f}, t {a[5]:.1f}")
    print(f"   second half: {b[0]:5} trades {b[1]:+.4f}% a trade, win {b[2]:.0f}%, worst {b[3]:+.2f}%, ${b[4]:+,.0f}, t {b[5]:.1f}")
    print(f"\n   {'target / stop':22} | H1 n   a trade   win     $      t | H2 n   a trade   win     $      t")
    scored = {}
    for tp, sp in product(sd.TARGETS, sd.STOPS):
        rows = []
        for s in NAMES:
            rows += sd.trades(data[s], every_day, tp, sp)
        a = st.summary([r for r in rows if r[0] < st.SPLIT])
        b = st.summary([r for r in rows if r[0] >= st.SPLIT])
        scored[(tp, sp)] = (a, b)
        print(f"   +${tp * 20:.0f} / -${sp * 20:.0f} ({tp}%/{sp}%) {'':3}| {a[0]:4} {a[1]:+.4f}% {a[2]:3.0f}% {a[4]:+7,.0f} {a[5]:5.1f} | "
              f"{b[0]:4} {b[1]:+.4f}% {b[2]:3.0f}% {b[4]:+7,.0f} {b[5]:5.1f}")
    ok_pairs = [k for k, (a, _) in scored.items() if a[0] >= 100]
    best = max(ok_pairs, key=lambda k: scored[k][0][5])
    b = scored[best][1]
    ok = b[1] > 0 and b[5] >= 3
    print(f"\n   SELECTED on the first half: +${best[0] * 20:.0f} / -${best[1] * 20:.0f} -> second half "
          f"{b[1]:+.4f}% a trade, ${b[4]:+,.0f}, t {b[5]:.1f}: {'PASS' if ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
