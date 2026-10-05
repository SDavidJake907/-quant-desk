"""Support and resistance zones from price, with RSI as context.

    python scripts/levels.py QCOM MRVL
    python scripts/levels.py SNPS --years 2 --k 3

READ-ONLY. Places no order.

ZONES COME FROM PRICE, NOT FROM RSI
RSI measures momentum on 0-100; it holds no price level. A zone is where price
has turned before: daily swing highs and lows (a bar beyond the k bars either
side, confirmed k bars later -- features/structure.swings), merged when they
sit within one daily ATR of each other, ranked by how many turns they hold.

RSI IS SHOWN, NOT TRUSTED
Today's 14-day RSI and the RSI at each zone's most recent touch, for reading
the approach. RSI divergence AT the sweep was tested here and flipped: setups
WITHOUT it did better, +0.724R vs +0.261R (README). This is a map, not a signal.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.features.bars import fetch_bars
from scanner.features.structure import HIGH, LOW, swings

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, CYAN = "\033[32m", "\033[31m", "\033[36m"


def rsi_series(closes: list[float], n: int = 14) -> list[float | None]:
    """Wilder RSI, recursive from the start of the series."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, n + 1)]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, n + 1)]
    ag, al = sum(gains) / n, sum(losses) / n
    for i in range(n, len(closes)):
        if i > n:
            ch = closes[i] - closes[i - 1]
            ag = (ag * (n - 1) + max(ch, 0)) / n
            al = (al * (n - 1) + max(-ch, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def atr(bars, n: int = 14) -> float:
    trs = [max(bars.highs[i] - bars.lows[i], abs(bars.highs[i] - bars.closes[i - 1]),
               abs(bars.lows[i] - bars.closes[i - 1])) for i in range(len(bars) - n, len(bars))]
    return sum(trs) / n


def zones(bars, k: int, width: float, rsi):
    """Clusters of confirmed pivots: (low, high, touches, last index, kinds)."""
    confirmed = [s for s in swings(bars.highs, bars.lows, k) if s.index + k < len(bars)]
    pts = sorted(confirmed, key=lambda s: s.price)
    groups: list[list] = []
    for s in pts:
        # The whole zone stays within one ATR of its lowest pivot. Comparing
        # only to the previous pivot chains a staircase of levels into one band.
        if groups and s.price - groups[-1][0].price <= width:
            groups[-1].append(s)
        else:
            groups.append([s])
    out = []
    for g in groups:
        last = max(g, key=lambda s: s.index)
        out.append({"lo": min(s.price for s in g), "hi": max(s.price for s in g),
                    "touches": len(g), "last": last.index, "rsi": rsi[last.index],
                    "highs": sum(s.kind == HIGH for s in g), "lows": sum(s.kind == LOW for s in g)})
    return out


def show(bars, k: int) -> None:
    rsi = rsi_series(list(bars.closes))
    price, a = bars.closes[-1], atr(bars)
    zs = zones(bars, k, a, rsi)
    now_rsi = rsi[-1]
    state = ("overbought (>70)" if now_rsi and now_rsi > 70 else
             "oversold (<30)" if now_rsi and now_rsi < 30 else "neutral")
    print(f"\n{BOLD}{bars.symbol}{RESET}  {price:.2f} on {bars.dates[-1]}   daily ATR {a:.2f} "
          f"({a / price * 100:.1f}%)   RSI(14) {now_rsi:.1f} {DIM}{state}{RESET}")
    above = sorted([z for z in zs if z["lo"] > price], key=lambda z: z["lo"])[:4]
    below = sorted([z for z in zs if z["hi"] < price], key=lambda z: -z["hi"])[:4]
    inside = [z for z in zs if z["lo"] <= price <= z["hi"]]
    print(f"  {'zone':<20}{'distance':>10}{'turns':>7}{'  (H/L)':<9}{'last touch':<13}{'RSI then':>9}")

    def row(z, tint, label):
        mid = (z["lo"] + z["hi"]) / 2
        dist = (mid / price - 1) * 100
        rng = f"{z['lo']:.2f}-{z['hi']:.2f}" if z["hi"] - z["lo"] > 0.005 else f"{z['lo']:.2f}"
        r = f"{z['rsi']:.0f}" if z["rsi"] is not None else "-"
        print(f"  {tint}{rng:<20}{RESET}{dist:>+9.1f}%{z['touches']:>7}  "
              f"({z['highs']}/{z['lows']})  {str(bars.dates[z['last']]):<13}{r:>9}  {DIM}{label}{RESET}")

    for z in reversed(above):
        row(z, RED, "resistance")
    for z in inside:
        row(z, CYAN, "price is inside this zone")
    print(f"  {BOLD}{'-- price ' + format(price, '.2f'):<20}{RESET}")
    for z in below:
        row(z, GREEN, "support")
    if above and below:
        up = (above[0]["lo"] / price - 1) * 100
        down = (1 - below[0]["hi"] / price) * 100
        print(f"  nearest resistance {up:+.1f}% away, nearest support -{down:.1f}% away "
              f"-> room {up / down:.1f}:1 if bought here with the stop under support")


async def main(symbols: list[str], years: int, k: int) -> int:
    async with httpx.AsyncClient(timeout=60) as client:
        for symbol in symbols:
            try:
                bars = await fetch_bars(symbol, client, range_=f"{years}y")
            except Exception as exc:  # noqa: BLE001
                print(f"{symbol}: no data ({exc})")
                continue
            show(bars, k)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="+")
    p.add_argument("--years", type=int, default=1)
    p.add_argument("--k", type=int, default=3, help="bars either side to confirm a pivot")
    a = p.parse_args()
    raise SystemExit(asyncio.run(main([s.upper() for s in a.symbols], a.years, a.k)))
