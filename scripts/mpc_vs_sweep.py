"""MPC against the live sweep strategy: same periods, same account, same sizing.

    python scripts/mpc_vs_sweep.py

READ-ONLY research. Both strategies from the 2y Alpaca store, 10bps costs,
Friday flatten, live price and $20M/bar gates. Per-trade R over the SAME
calendar halves, then each run alone through sizing_research's account
simulation with the live sizing (1% risk, 20% cap, 5 slots, cash only).
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import mpc_test
import sizing_research


def stats(v):
    if not v:
        return "n=0"
    m = statistics.fmean(v)
    return f"{m:+.3f}R  n={len(v):<5} win {sum(x > 0 for x in v) / len(v) * 100:.0f}%"


async def main() -> int:
    from scanner.features.bars import fetch_many
    symbols = sorted({f.name.split("__")[0] for f in Path(mpc_test.STORE).glob("*.json")})
    async with httpx.AsyncClient(timeout=60) as client:
        daily, _ = await fetch_many(tuple(symbols), client, range_="5y")
    with ProcessPoolExecutor() as pool:
        mpc = [r for c in pool.map(mpc_test._work,
                                   [(s, (list(daily[s].dates), list(daily[s].closes)) if s in daily else None)
                                    for s in symbols], chunksize=8) for r in c if r["retested"]]
        sweep = [r for c in pool.map(sizing_research._work, [(s, None, None) for s in symbols],
                                     chunksize=8) for r in c]
    mpc = [{**r, "at": r["entry_at"], "r": r["r_retest"]} for r in mpc]
    for rows in (mpc, sweep):
        rows.sort(key=lambda r: r["at"])
    start = min(mpc[0]["at"], sweep[0]["at"])
    end = max(mpc[-1]["at"], sweep[-1]["at"])
    mid = start + (end - start) / 2
    print(f"\nsame calendar halves: {start:%Y-%m-%d} .. {mid:%Y-%m-%d} .. {end:%Y-%m-%d}")
    print(f"  {'':<22}{'FIRST HALF':<34}{'SECOND HALF':<34}")
    for name, rows in (("SWEEP (live)", sweep), ("MPC (retest)", mpc)):
        a = [r["r"] for r in rows if r["at"] < mid]
        b = [r["r"] for r in rows if r["at"] >= mid]
        print(f"  {name:<22}{stats(a):<34}{stats(b):<34}")
    print("\nthe account, live sizing (1% risk, 20% cap, 5 slots, cash, 10bps, Friday flatten):")
    print(f"  {'':<16}{'return':>9}{'max DD':>9}{'ret/DD':>8}{'taken':>7}")

    def current(t, eq):
        return min(eq * 0.01 / (t["entry"] - t["stop"]) * t["entry"], eq * 0.20)

    for label, part_of in (("2 YEARS", lambda r: True), ("FIRST HALF", lambda r: r["at"] < mid),
                           ("SECOND HALF", lambda r: r["at"] >= mid)):
        print(f"  {label}")
        for name, rows in (("SWEEP (live)", sweep), ("MPC (retest)", mpc)):
            ret, dd, ratio, _, _, taken = sizing_research.simulate([r for r in rows if part_of(r)], current)
            print(f"    {name:<14}{ret:>+8.1f}%{dd:>8.1f}%{ratio:>8.2f}{taken:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
