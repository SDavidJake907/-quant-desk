"""TRIALS #83: Alpaca's example scalper (github.com/alpacahq/example-scalping), on 1-minute candles.

Operator, 2026-10-22: "this is the closes i came to one [a pro scalper]" -- test it.
Read 2026-10-22 (main.py, June 2021). Its rules, as written there:
  buy signal  1-minute close crosses ABOVE the 20-minute SMA of the session's
              candles (close[-2] < sma[-2] and close[-1] > sma[-1]); needs 21
              candles after 9:30; no new buys from 15:55
  buy order   limit at the last trade; cancelled if unfilled after 2 minutes
  sell order  the moment the buy fills: limit at max(entry + $0.01, current
              price), left until filled; at 15:55 everything sold at market
  no stop     (its README: "this may cause loss more than the accumulated profit")

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  data       Alpaca SIP 1-minute candles, regular hours, 2025-10-01 -> the last
             session (collect_alpaca.fetch_symbol, cached in data/research_1m)
  names      BIG: the 20 largest companies (signal_lab.BIGCAP); FAST: each
             month the 20 S&P 500 names with the highest 20-day ATR % at the
             prior month's end (daily Yahoo bars), traded only in that month
  fills      CONSERVATIVE, as a limit order waits in a queue: a buy at L fills
             only if a later candle trades BELOW L (low < L), within the next 2
             candles; the sell limit S fills on the first later candle with
             high > S, at S. "Current price" at the fill = that candle's close.
             Market exits (15:55, stop) fill at that candle's close less 0.02%.
  versions   A  the original: no stop
             B  A plus a stop: low <= entry - 3 x ATR(14) of the 1-minute
                candles -> out at the stop (or the open, if it gapped through)
  measure    net % a trade ($2,000 lot, its default); per-day sums; halves split
             at 2026-04-15
  PASS       (for B; A is reported) mean net a trade > 0 with t >= 2 over days,
             in BOTH halves.
READ-ONLY research. Changes nothing live.
"""

from __future__ import annotations

import asyncio
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import collect_alpaca  # noqa: E402
import signal_lab as lab  # noqa: E402

from scanner.features.bars import fetch_many  # noqa: E402
from scanner.features.intraday import EXCHANGE_TZ  # noqa: E402
from scanner.signals import indicators as ind  # noqa: E402
from scanner.sources import composite  # noqa: E402
from scanner.sources.index_history import SP500  # noqa: E402

START = "2025-10-01"
SPLIT = date(2026, 4, 15)
STORE = ROOT / "data/research_1m"
FAST_N = 20
LOT = 2000.0
LAST_BUY, BAILOUT = time(15, 55), time(15, 55)
SLIP = 0.0002


async def fast_by_month(client) -> dict[tuple[int, int], set[str]]:
    members = await composite.load_universe(client, (SP500,))
    bars, _ = await fetch_many(tuple(m.symbol for m in members), client, range_="2y")
    rows = {s: [(d, o, h, lo, c) for d, o, h, lo, c in zip(b.dates, b.opens, b.highs, b.lows, b.closes)]
            for s, b in bars.items()}
    months = lab.fast_months(rows, FAST_N)
    return {m: v for m, v in months.items() if (m[0], m[1]) >= (2025, 10)}


async def candles(client, symbols) -> dict[str, list]:
    STORE.mkdir(parents=True, exist_ok=True)
    out = {}
    for s in sorted(symbols):
        f = STORE / f"{s}.json"
        if f.exists():
            rows = json.loads(f.read_text())
        else:
            rows = await collect_alpaca.fetch_symbol(client, s, START, minutes=1)
            if isinstance(rows, str):
                print(f"  {s}: {rows}")
                continue
            f.write_text(json.dumps(rows))
        out[s] = [(datetime.fromtimestamp(t, EXCHANGE_TZ).replace(tzinfo=None), o, h, lo, c, v)
                  for t, o, h, lo, c, v in rows]
        print(f"  {s}: {len(out[s])} candles", flush=True)
    return out


def trades(bars, allowed, stop: bool) -> list[tuple[date, float]]:
    """(day, net %) for each completed trade, one day at a time."""
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
        atr = ind.atr(h, lo, c, 14)
        i = 21
        while i < len(rows) - 1:
            if rows[i][0].time() >= LAST_BUY or sma[i - 1] is None:
                break
            if not (c[i - 1] < sma[i - 1] and c[i] > sma[i]):
                i += 1
                continue
            limit = c[i]
            fill = next((j for j in (i + 1, i + 2) if j < len(rows) and lo[j] < limit), None)
            if fill is None:
                i += 1
                continue
            entry = limit
            target = max(entry + 0.01, c[fill])
            stop_px = entry - 3 * atr[fill] if (stop and atr[fill]) else None
            exit_px, j = None, fill + 1
            while j < len(rows):
                if stop_px is not None and lo[j] <= stop_px:
                    exit_px = min(rows[j][1], stop_px)
                    break
                if h[j] > target:
                    exit_px = target
                    break
                if rows[j][0].time() >= BAILOUT or j == len(rows) - 1:
                    exit_px = c[j] * (1 - SLIP)
                    break
                j += 1
            if exit_px is None:
                break
            out.append((day, (exit_px / entry - 1) * 100))
            i = j + 1
    return out


def summary(rows):
    by_day = defaultdict(float)
    for d, r in rows:
        by_day[d] += r * LOT / 100
    days = list(by_day.values())
    t = (statistics.mean(days) / statistics.stdev(days) * math.sqrt(len(days))) if len(days) > 2 and statistics.stdev(days) else float("nan")
    nets = [r for _, r in rows]
    return (len(nets), statistics.mean(nets) if nets else float("nan"),
            sum(n > 0 for n in nets) / len(nets) * 100 if nets else float("nan"),
            min(nets) if nets else float("nan"), sum(days), t)


async def main() -> int:
    async with httpx.AsyncClient(timeout=120) as client:
        fast = await fast_by_month(client)
        names = set(lab.BIGCAP) | set().union(*fast.values())
        print(f"{len(names)} names; fetching 1-minute candles from {START}")
        data = await candles(client, names)
    groups = {"BIG": lambda s: (lambda d: s in lab.BIGCAP),
              "FAST": lambda s: (lambda d: s in fast.get((d.year, d.month), ()))}
    passes = 0
    for gname, gate in groups.items():
        for version, stop in (("A original, no stop", False), ("B with 3xATR stop", True)):
            rows = []
            for s, bars in data.items():
                rows += trades(bars, gate(s), stop)
            print(f"\n{gname} -- {version}")
            for half, label in ((True, "to 2026-04-14"), (False, "from 2026-04-15")):
                n, mean, win, worst, dollars, t = summary([r for r in rows if (r[0] < SPLIT) == half])
                print(f"  {label}: {n} trades, {mean:+.3f}% a trade, win {win:.0f}%, worst {worst:+.2f}%, "
                      f"${dollars:+,.0f} on ${LOT:,.0f} lots, t {t:.1f}")
            if stop:
                a = summary([r for r in rows if r[0] < SPLIT])
                b = summary([r for r in rows if r[0] >= SPLIT])
                ok = a[1] > 0 and a[5] >= 2 and b[1] > 0 and b[5] >= 2
                passes += ok
                print(f"  B: {'PASS' if ok else 'FAIL'}")
    print(f"\nPASS: {passes} of 2 (BIG, FAST)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
