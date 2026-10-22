"""TRIALS #100: the operator's ROTATION book -- sell a holding when it is at its high, buy one that is ready to rise.

Operator, 2026-10-22: "when it at its highest, sell, and contine to look for stocks that are rising
or ready to rise ... these cant be the only stocks rising."

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  data      Yahoo daily 10y, today's S&P 500 members (sweep_slow.daily_series)
  book      SLOTS = 5 equal slots, always looking to be full. Each day after the close:
            SELL a holding when its EXIT signal fires; then fill every empty slot with
            the top-ranked ENTRY candidates not held. Trades at the NEXT open, 0.05% a side.
  EXIT      RSI14   RSI(14) > 70
            CRSI    Connors RSI > 70
            BB      close above the upper Bollinger band (20, 2)
            plus a 3 x ATR(22) stop from the entry (every desk rule has one)
  ENTRY     READY   "ready to rise": RSI(2) < 10 and close > SMA(200); ranked by lowest RSI(2)
            RISING  "starting to rise": close crossed above SMA(50) today and 63-day return
                    > 0; ranked by 63-day return
  versions  3 exits x 2 entries = 6
  measure   the book's daily return minus the S&P 500's (SPY) daily return; t over days
  halves    2016-10 -> 2020-12 / 2021-01 -> 2026-10
  SELECT    the version with the highest half-1 t; PASS: that version in half 2,
            mean daily excess > 0 with t >= 3
  checks    CHEAT (enter only stocks that rise tomorrow) must beat SPY by a lot; RANDOM
            (random entries, same exits) must not beat it -- else the run is void
READ-ONLY research.
"""

from __future__ import annotations

import asyncio
import math
import random
import statistics
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import sweep_slow as ss  # noqa: E402

from scanner.features.bars import fetch_bars  # noqa: E402
from scanner.signals import indicators as ind  # noqa: E402

SLOTS = 5
COST = 0.0005
SPLIT = date(2021, 1, 1)


def prepare(series):
    """symbol -> per-date dict of what the rules need."""
    out = {}
    for s, b in series.items():
        if len(b) < 260:
            continue
        d = [x[0] for x in b]
        o, h, lo, c, v = ([x[k] for x in b] for k in range(1, 6))
        r14, r2, crsi = ind.rsi(c, 14), ind.rsi(c, 2), ind.connors_rsi(c)
        s20, s50, s200 = ind.sma(c, 20), ind.sma(c, 50), ind.sma(c, 200)
        atr = ind.atr(h, lo, c, 22)
        rows = {}
        for i in range(len(c)):
            sd = None
            if i >= 19:
                w = c[i - 19:i + 1]
                m = sum(w) / 20
                sd = math.sqrt(sum((x - m) ** 2 for x in w) / 20)
            rows[d[i]] = {
                "o": o[i], "c": c[i], "lo": lo[i], "atr": atr[i], "next_o": o[i + 1] if i + 1 < len(c) else None,
                "next_c": c[i + 1] if i + 1 < len(c) else None,
                "x_RSI14": r14[i] is not None and r14[i] > 70,
                "x_CRSI": crsi[i] is not None and crsi[i] > 70,
                "x_BB": sd is not None and c[i] > s20[i] + 2 * sd,
                "e_READY": (r2[i] is not None and s200[i] is not None and r2[i] < 10 and c[i] > s200[i]),
                "rank_READY": -(r2[i] or 100),
                "e_RISING": (i > 63 and s50[i] is not None and s50[i - 1] is not None and c[i] > s50[i]
                             and c[i - 1] <= s50[i - 1] and c[i] > c[i - 63]),
                "rank_RISING": (c[i] / c[i - 63] - 1) if i > 63 else -9,
            }
        out[s] = rows
    return out


def simulate(data, days, spy, exit_k, entry_k, rnd=None, cheat=False):
    """Daily book return minus SPY's (in %), per day. Each of SLOTS slots is tracked on its own:
    decisions after day t's close fill at day t+1's open; a sale and a new buy in the same slot
    on the same morning compound; an empty slot earns 0 (cash)."""
    slots: list[dict | None] = [None] * SLOTS
    exits: set[int] = set()          # slot indices to sell at the next open
    entries: dict[int, str] = {}     # slot index -> symbol to buy at the next open
    excess = {}
    for t in range(1, len(days)):
        day, prev = days[t], days[t - 1]
        rets = []
        for k in range(SLOTS):
            pos = slots[k]
            r = 0.0
            if pos is not None:
                cur, old = data[pos["s"]].get(day), data[pos["s"]].get(prev)
                if cur is None or old is None:
                    r = 0.0
                elif k in exits:
                    r = cur["o"] / old["c"] - 1 - COST          # sold at today's open
                    slots[k] = None
                else:
                    r = cur["c"] / old["c"] - 1
            if k in entries:
                cur = data[entries[k]].get(day)
                if cur is not None:
                    r = (1 + r) * (cur["c"] / cur["o"] - COST) - 1    # bought at today's open
                    slots[k] = {"s": entries[k], "stop": cur["o"] - 3 * (data[entries[k]][prev]["atr"] or 0)}
            rets.append(r)
        exits, entries = set(), {}
        if day in spy:
            excess[day] = (sum(rets) / SLOTS - spy[day]) * 100
        # after today's close: exits, then fill the slots that will be free
        for k, pos in enumerate(slots):
            if pos is None:
                continue
            r = data[pos["s"]].get(day)
            if r is not None and (r[f"x_{exit_k}"] or r["lo"] <= pos["stop"]):
                exits.add(k)
        free = [k for k in range(SLOTS) if slots[k] is None or k in exits]
        if not free or t + 1 >= len(days):
            continue
        held = {p["s"] for p in slots if p is not None}
        cands = []
        for s, rows in data.items():
            r = rows.get(day)
            if s in held or r is None or r["next_o"] is None or r["atr"] is None:
                continue
            if cheat:
                if r["next_c"] > r["next_o"] * 1.01:
                    cands.append((1.0, s))
            elif rnd is not None:
                if rnd.random() < 0.01:
                    cands.append((rnd.random(), s))
            elif r[f"e_{entry_k}"]:
                cands.append((r[f"rank_{entry_k}"], s))
        cands.sort(reverse=True)
        for k, (_, s) in zip(free, cands):
            entries[k] = s
    return excess


def tstat(xs):
    return statistics.mean(xs) / statistics.stdev(xs) * math.sqrt(len(xs)) if len(xs) > 2 and statistics.stdev(xs) else float("nan")


def halves(ex):
    a = [v for d, v in ex.items() if d < SPLIT]
    b = [v for d, v in ex.items() if d >= SPLIT]
    return a, b


def main() -> int:
    series = asyncio.run(ss.daily_series())

    async def spy_bars():
        import httpx
        async with httpx.AsyncClient(timeout=60) as c:
            return await fetch_bars("SPY", c, range_="10y")
    sb = asyncio.run(spy_bars())
    spy = {sb.dates[i]: sb.closes[i] / sb.closes[i - 1] - 1 for i in range(1, len(sb.closes))}
    data = prepare(series)
    days = sorted({d for rows in data.values() for d in rows if d in spy})
    print(f"{len(data)} names, {len(days)} days")
    results = {}
    for exit_k in ("RSI14", "CRSI", "BB"):
        for entry_k in ("READY", "RISING"):
            a, b = halves(simulate(data, days, spy, exit_k, entry_k))
            results[(exit_k, entry_k)] = (a, b)
            print(f"  sell {exit_k:5} buy {entry_k:6} | 2016-20: {statistics.mean(a):+.4f}%/day vs SPY "
                  f"({statistics.mean(a) * 252:+.1f}%/yr) t {tstat(a):5.1f} | 2021-26: {statistics.mean(b):+.4f}%/day "
                  f"({statistics.mean(b) * 252:+.1f}%/yr) t {tstat(b):5.1f}", flush=True)
    for name, kw in (("CHEAT", {"cheat": True}), ("RANDOM", {"rnd": random.Random(100)})):
        a, b = halves(simulate(data, days, spy, "RSI14", "READY", **kw))
        print(f"  {name:6} (sell RSI14)  | 2016-20: {statistics.mean(a) * 252:+.1f}%/yr t {tstat(a):5.1f} | "
              f"2021-26: {statistics.mean(b) * 252:+.1f}%/yr t {tstat(b):5.1f}", flush=True)
    best = max(results, key=lambda k: tstat(results[k][0]))
    b = results[best][1]
    ok = statistics.mean(b) > 0 and tstat(b) >= 3
    print(f"\nSELECTED on 2016-20: sell {best[0]}, buy {best[1]} -> 2021-26 {statistics.mean(b) * 252:+.1f}%/yr vs SPY, "
          f"t {tstat(b):.1f}: {'PASS' if ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
