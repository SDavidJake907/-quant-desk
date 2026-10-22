"""Four-factor report: how much of each book's return is skill (alpha) vs market, size, value, momentum.

    python scripts/factor_report.py      # writes logs/factors/YYYY-MM-DD.md

Rules in scanner/ops/factors.py. Runs Fridays after the close (stock-scanner-factors).
Read-only: books from data/competition/snapshots.jsonl, ETFs from Yahoo.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.features.bars import fetch_bars
from scanner.ops import competition as comp
from scanner.ops import factors as fx

SNAPS = Path("data/competition/snapshots.jsonl")
OUT = Path("logs/factors")
ETFS = ("SPY", "IWM", "IWD", "IWF", "MTUM")
BOOKS = {comp.GEMMA: "Gemma", comp.PICKS: "You (picks)"}
WORDS = {"MKT": "market", "SMB": "small caps", "HML": "value stocks", "MOM": "momentum"}


def explain(f: fx.Fit) -> list[str]:
    lines = [f"- **Alpha (skill): {f.alpha_yearly:+.1f}% a year**, t {f.alpha_t:.1f} "
             + ("-- clearly positive." if f.alpha_t >= 2 else "-- clearly negative." if f.alpha_t <= -2
                else "-- not yet distinguishable from zero.")]
    for k in fx.FACTORS:
        b, t = f.betas[k], f.beta_t[k]
        if abs(t) >= 2:
            lean = "leans toward" if b > 0 else "leans against"
            lines.append(f"- {lean} **{WORDS[k]}**: loading {b:+.2f} (t {t:.1f})")
    lines.append(f"- The four factors explain **{f.r2 * 100:.0f}%** of its daily swings.")
    return lines


async def main() -> int:
    async with httpx.AsyncClient(timeout=60) as client:
        bars = {s: await fetch_bars(s, client, range_="1y") for s in ETFS}
        irx = await fetch_bars("^IRX", client, range_="1y")
    closes = {s: dict(zip(b.dates, b.closes)) for s, b in bars.items()}
    factors = fx.factor_returns(closes, dict(zip(irx.dates, irx.closes)))
    snaps = comp.load_snapshots(SNAPS.read_text(encoding="utf-8")) if SNAPS.exists() else []
    today = date.today()
    lines = [f"# Four-factor report {today}", "",
             "Carhart model: each book's daily return vs cash, split into the market, small caps (IWM-SPY), "
             "value (IWD-IWF), momentum (MTUM-SPY), and **alpha** -- the part none of them explains. "
             f"A first look needs {fx.MIN_DAYS} trading days; about {fx.RELIABLE_DAYS} (6 months) before alpha means much.", ""]
    for book, name in BOOKS.items():
        rets = fx.book_returns(snaps, book)
        lines += [f"## {name}", ""]
        try:
            f = fx.attribute(rets, factors)
        except ValueError as exc:
            n = len([d for d in rets if d in factors])
            lines += [f"Not enough data yet ({n} trading days): {exc}.", ""]
            continue
        lines += [f"{f.days} trading days" + (" -- EARLY: treat as a first look." if f.days < fx.RELIABLE_DAYS else ""), ""]
        lines += explain(f) + [""]
    text = "\n".join(lines) + "\n"
    print(text)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{today.isoformat()}.md").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
