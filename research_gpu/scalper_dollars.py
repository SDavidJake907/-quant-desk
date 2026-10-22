"""TRIALS #84: the example scalper's entry with DOLLAR exits instead of one cent.

Operator, 2026-10-22: "how can we edit this, not pennys but dollors ammounts?" -> "Yes, test it."

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  entry      exactly TRIALS #83: 1-minute close crosses above the 20-minute SMA,
             limit buy at that close filled only if a candle in the next 2 trades
             below it; no buys from 15:55; one trade at a time per name
  exits      a TARGET and a STOP, both in dollars on the $2,000 lot (= %):
             target +$5 / $10 / $20 / $40 (0.25 / 0.5 / 1 / 2 %),
             stop   -$10 / $20 / $40 (0.5 / 1 / 2 %) -> 12 pairs.
             Each candle after the fill: stop first (low <= stop: out at the stop,
             or the open if it gapped through, less 0.02%), then target (high >
             target: out at the target); 15:55 or the day's last candle: out at
             the close less 0.02%.
  data/names as #83 (data/research_1m, 2025-10-01 on; BIG and monthly FAST)
  SELECT     per group, on the first half (to 2026-04-14) only: the pair with the
             highest t of daily dollars, >= 200 trades
  PASS       that pair in the second half: mean net a trade > 0, t >= 2 over days.
READ-ONLY research.
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from datetime import date
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import httpx  # noqa: E402
import scalper_test as st  # noqa: E402
import signal_lab as lab  # noqa: E402

from scanner.signals import indicators as ind  # noqa: E402

TARGETS = (0.25, 0.5, 1.0, 2.0)
STOPS = (0.5, 1.0, 2.0)
MIN_TRADES = 200


def trades(bars, allowed, target_pct: float, stop_pct: float) -> list[tuple[date, float]]:
    by_day: dict[date, list] = defaultdict(list)
    for b in bars:
        by_day[b[0].date()].append(b)
    out = []
    for day, rows in sorted(by_day.items()):
        if not allowed(day):
            continue
        h = [r[2] for r in rows]
        lo = [r[3] for r in rows]
        c = [r[4] for r in rows]
        sma = ind.sma(c, 20)
        i = 21
        while i < len(rows) - 1:
            if rows[i][0].time() >= st.LAST_BUY or sma[i - 1] is None:
                break
            if not (c[i - 1] < sma[i - 1] and c[i] > sma[i]):
                i += 1
                continue
            entry = c[i]
            fill = next((j for j in (i + 1, i + 2) if j < len(rows) and lo[j] < entry), None)
            if fill is None:
                i += 1
                continue
            tgt, stp = entry * (1 + target_pct / 100), entry * (1 - stop_pct / 100)
            exit_px, j = None, fill + 1
            while j < len(rows):
                if lo[j] <= stp:
                    exit_px = min(rows[j][1], stp) * (1 - st.SLIP)
                    break
                if h[j] > tgt:
                    exit_px = tgt
                    break
                if rows[j][0].time() >= st.BAILOUT or j == len(rows) - 1:
                    exit_px = c[j] * (1 - st.SLIP)
                    break
                j += 1
            if exit_px is None:
                break
            out.append((day, (exit_px / entry - 1) * 100))
            i = j + 1
    return out


async def main() -> int:
    async with httpx.AsyncClient(timeout=120) as client:
        fast = await st.fast_by_month(client)
        names = set(lab.BIGCAP) | set().union(*fast.values())
        data = await st.candles(client, names)
    groups = {"BIG": lambda s: (lambda d: s in lab.BIGCAP),
              "FAST": lambda s: (lambda d: s in fast.get((d.year, d.month), ()))}
    passes = 0
    for gname, gate in groups.items():
        print(f"\n== {gname}: target / stop pairs, first half (selection) -> second half (judged) ==")
        print(f"   {'pair':22} | H1 n   a trade  win    $       t | H2 n   a trade  win    $       t")
        scored = {}
        for tp, sp in product(TARGETS, STOPS):
            rows = []
            for s, bars in data.items():
                rows += trades(bars, gate(s), tp, sp)
            a = st.summary([r for r in rows if r[0] < st.SPLIT])
            b = st.summary([r for r in rows if r[0] >= st.SPLIT])
            scored[(tp, sp)] = (a, b)
            label = f"+${tp * 20:.0f} / -${sp * 20:.0f} ({tp}%/{sp}%)"
            print(f"   {label:22} | {a[0]:5} {a[1]:+.3f}% {a[2]:3.0f}% {a[4]:+8,.0f} {a[5]:5.1f} | "
                  f"{b[0]:5} {b[1]:+.3f}% {b[2]:3.0f}% {b[4]:+8,.0f} {b[5]:5.1f}", flush=True)
        ok_pairs = [k for k, (a, _) in scored.items() if a[0] >= MIN_TRADES]
        best = max(ok_pairs, key=lambda k: scored[k][0][5])
        b = scored[best][1]
        ok = b[1] > 0 and b[5] >= 2
        passes += ok
        print(f"   SELECTED on the first half: +${best[0] * 20:.0f} / -${best[1] * 20:.0f} -> second half "
              f"{b[1]:+.3f}% a trade, ${b[4]:+,.0f}, t {b[5]:.1f}: {'PASS' if ok else 'FAIL'}")
    print(f"\nPASS: {passes} of 2")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
