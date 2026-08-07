"""Pull the setups where divergence and outcome disagree most, and draw them.

    python scripts/divergence_cases.py

The statistical test said RSI divergence does not predict outcome on this
setup. That is a claim about averages, and averages are the wrong tool for
answering "but I can SEE it on the chart" — a scalar difference between two
pivot RSI values is not what a person reads when they look at a chart. They
read slope across several swings, which lows are structurally significant, and
where the whole thing sits in a larger move.

So this writes the disagreement cases out as actual charts: the strongest
divergences that LOST, and the setups with no divergence at all that WON. If
the eye is picking up something the scalar misses, it will be visible there,
and whatever it is can then be measured properly instead of argued about.

Output is a self-contained SVG per case, no plotting library.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib

import httpx

from scanner.backtest.setups import resolve
from scanner.features.intraday import fetch_intraday, max_range
from scanner.features.intraday_context import rsi_series
from scanner.features.structure import HIGH, LOW, known_at, swings
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}

W, PRICE_H, RSI_H, PAD = 1180, 330, 150, 54


def _scale(values, lo, hi, top, height):
    span = (hi - lo) or 1.0
    return [top + height - (v - lo) / span * height for v in values]


def draw(case: dict) -> str:
    """One case: candles, marked pivots, the divergence lines, and the trade."""
    highs, lows, closes = case["highs"], case["lows"], case["closes"]
    opens = [closes[0]] + closes[:-1]
    rsis = case["rsi"]
    n = len(closes)
    step = (W - 2 * PAD) / max(1, n)
    body = max(1.6, step * 0.6)

    lo = min(min(lows), case["stop"]) * 0.998
    hi = max(max(highs), case["target"]) * 1.002
    ys_h = _scale(highs, lo, hi, PAD, PRICE_H)
    ys_l = _scale(lows, lo, hi, PAD, PRICE_H)
    ys_o = _scale(opens, lo, hi, PAD, PRICE_H)
    ys_c = _scale(closes, lo, hi, PAD, PRICE_H)

    rtop = PAD + PRICE_H + 46
    known = [v if v is not None else 50.0 for v in rsis]
    ys_r = _scale(known, 0, 100, rtop, RSI_H)

    def x(i):
        return PAD + i * step + step / 2

    p = []
    for i in range(n):
        colour = "#2e9e5b" if closes[i] >= opens[i] else "#d4483b"
        top, bot = min(ys_o[i], ys_c[i]), max(ys_o[i], ys_c[i])
        p.append(f'<line x1="{x(i):.1f}" y1="{ys_h[i]:.1f}" x2="{x(i):.1f}" '
                 f'y2="{ys_l[i]:.1f}" stroke="{colour}" stroke-width="1"/>')
        p.append(f'<rect x="{x(i)-body/2:.1f}" y="{top:.1f}" width="{body:.1f}" '
                 f'height="{max(1.0, bot-top):.1f}" fill="{colour}"/>')

    # RSI line, and the 30/50/70 guides
    pts = " ".join(f"{x(i):.1f},{ys_r[i]:.1f}" for i in range(n)
                   if rsis[i] is not None)
    for level in (30, 50, 70):
        y = rtop + RSI_H - level / 100 * RSI_H
        p.append(f'<line x1="{PAD}" y1="{y:.1f}" x2="{W-PAD}" y2="{y:.1f}" '
                 f'stroke="#9aa0a6" stroke-width="0.6" stroke-dasharray="3 4"/>')
        p.append(f'<text x="{W-PAD+6}" y="{y+4:.1f}" font-size="11" '
                 f'fill="#9aa0a6">{level}</text>')
    p.append(f'<polyline points="{pts}" fill="none" stroke="#3b6fd4" '
             f'stroke-width="1.6"/>')

    # the two lows the scalar compared, on both panels
    sw, pr = case["sweep"] - case["lo"], case["prior"] - case["lo"]
    for idx, colour in ((pr, "#9aa0a6"), (sw, "#d4483b")):
        if 0 <= idx < n:
            p.append(f'<circle cx="{x(idx):.1f}" cy="{ys_l[idx]:.1f}" r="5" '
                     f'fill="none" stroke="{colour}" stroke-width="2"/>')
            p.append(f'<circle cx="{x(idx):.1f}" cy="{ys_r[idx]:.1f}" r="5" '
                     f'fill="none" stroke="{colour}" stroke-width="2"/>')
    if 0 <= pr < n and 0 <= sw < n:
        for ya, yb in ((ys_l[pr], ys_l[sw]), (ys_r[pr], ys_r[sw])):
            p.append(f'<line x1="{x(pr):.1f}" y1="{ya:.1f}" x2="{x(sw):.1f}" '
                     f'y2="{yb:.1f}" stroke="#c026d3" stroke-width="1.8" '
                     f'stroke-dasharray="6 4"/>')

    # entry, stop, target
    for price, colour, label in ((case["entry"], "#1f2933", "entry"),
                                 (case["stop"], "#d4483b", "stop"),
                                 (case["target"], "#2e9e5b", "target")):
        y = _scale([price], lo, hi, PAD, PRICE_H)[0]
        p.append(f'<line x1="{PAD}" y1="{y:.1f}" x2="{W-PAD}" y2="{y:.1f}" '
                 f'stroke="{colour}" stroke-width="1" stroke-dasharray="5 5"/>')
        p.append(f'<text x="{PAD+4}" y="{y-5:.1f}" font-size="11" '
                 f'fill="{colour}">{label} {price:,.2f}</text>')

    tg = case["trig"] - case["lo"]
    if 0 <= tg < n:
        p.append(f'<line x1="{x(tg):.1f}" y1="{PAD}" x2="{x(tg):.1f}" '
                 f'y2="{PAD+PRICE_H}" stroke="#1f2933" stroke-width="1.2"/>')

    verdict = "WON" if case["r"] > 0 else "LOST"
    vcol = "#2e9e5b" if case["r"] > 0 else "#d4483b"
    head = (f'<text x="{PAD}" y="30" font-size="18" font-weight="600" '
            f'fill="#1f2933">{case["sym"]}</text>'
            f'<text x="{PAD+90}" y="30" font-size="14" fill="#c026d3">'
            f'RSI divergence {case["div"]:+.2f}</text>'
            f'<text x="{PAD+300}" y="30" font-size="14" fill="{vcol}">'
            f'{verdict} {case["r"]:+.2f}R</text>'
            f'<text x="{PAD}" y="{rtop-14}" font-size="12" fill="#5f6368">'
            f'RSI(14) — dashed magenta joins the same two lows as the price panel</text>')

    height = rtop + RSI_H + 40
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" '
            f'height="{height}" viewBox="0 0 {W} {height}">'
            f'<rect width="{W}" height="{height}" fill="#ffffff"/>'
            f'{head}{"".join(p)}</svg>')


def _prior_low(bars, setup, k):
    visible = [s for s in known_at(swings(bars.highs, bars.lows, k),
                                   setup.trigger_index, k) if s.kind == LOW]
    earlier = [s for s in visible if s.index < setup.sweep_index]
    return earlier[-1].index if earlier else None


async def run(index: str, minutes: int, k: int) -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])
        symbols = [m.symbol for m in universe]
        print(f"fetching {len(symbols)} symbols")
        frames = await asyncio.gather(
            *(fetch_intraday(s, client, minutes=minutes, range_=max_range(minutes))
              for s in symbols), return_exceptions=True)

    cases = []
    for bars in frames:
        if isinstance(bars, Exception) or len(bars) < 60:
            continue
        rsis = rsi_series(bars.closes)
        for setup in scan(bars, k=k, require_value_sweep=True,
                          min_risk_pct=1.5, min_reward_risk=1.5):
            outcome = resolve(bars, symbol=bars.symbol,
                              entry_index=setup.trigger_index,
                              entry_price=setup.entry_price,
                              stop_price=setup.stop_price,
                              target_price=setup.target_price, gap_fills=True)
            if outcome is None or outcome.r_multiple is None:
                continue
            prior = _prior_low(bars, setup, k)
            if prior is None:
                continue
            a, b = rsis[setup.sweep_index], rsis[prior]
            if a is None or b is None:
                continue
            lo = max(0, min(prior, setup.sweep_index) - 12)
            hi = min(len(bars), setup.trigger_index + 22)
            cases.append(dict(
                sym=bars.symbol, r=outcome.r_multiple, div=a - b,
                lo=lo, sweep=setup.sweep_index, prior=prior,
                trig=setup.trigger_index, entry=setup.entry_price,
                stop=setup.stop_price, target=setup.target_price,
                highs=list(bars.highs[lo:hi]), lows=list(bars.lows[lo:hi]),
                closes=list(bars.closes[lo:hi]), rsi=list(rsis[lo:hi]),
            ))

    strong_lost = sorted([c for c in cases if c["div"] > 4 and c["r"] < -0.5],
                         key=lambda c: -c["div"])[:3]
    none_won = sorted([c for c in cases if c["div"] <= 0 and c["r"] > 1.5],
                      key=lambda c: -c["r"])[:3]

    out = pathlib.Path("tmp/divergence")
    out.mkdir(parents=True, exist_ok=True)
    print(f"\n{len(cases)} resolved setups")
    print("\nSTRONG divergence that LOST:")
    for n, c in enumerate(strong_lost, 1):
        (out / f"lost_{n}_{c['sym']}.svg").write_text(draw(c), encoding="utf-8")
        print(f"  {c['sym']:<6} divergence {c['div']:>+6.2f}  {c['r']:>+6.2f}R")
    print("\nNO divergence that WON:")
    for n, c in enumerate(none_won, 1):
        (out / f"won_{n}_{c['sym']}.svg").write_text(draw(c), encoding="utf-8")
        print(f"  {c['sym']:<6} divergence {c['div']:>+6.2f}  {c['r']:>+6.2f}R")
    print(f"\nwrote {len(strong_lost)+len(none_won)} charts to {out}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--minutes", type=int, default=30)
    p.add_argument("--k", type=int, default=2)
    a = p.parse_args()
    asyncio.run(run(a.index, a.minutes, a.k))
