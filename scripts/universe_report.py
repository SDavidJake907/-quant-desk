"""One report per stock universe -- READ-ONLY, trades nothing, changes nothing.

    python scripts/universe_report.py          # writes logs/universes/YYYY-MM-DD.md

Operator, 2026-10-19: "find all the Index and use the scanner for both, as a
different universe from each one". Step 1 of 2: see what each universe would
bring before any pool changes (step 2, after the Oct 31 verdict, via FREEZE.md).

Universes
  Large   S&P 500
  Mid     S&P MidCap 400
  Small   S&P SmallCap 600
  Nasdaq  Nasdaq-100 names in NO S&P index (MSTR left out: a bitcoin holder,
          and this desk trades stocks only)
  (The Dow 30 adds nothing: all 30 are in the S&P 500.)

Per universe: how many names, how many clear the floors (price >= $5, 20-day
average daily dollar volume >= $20M), the share above the 210-day average, and
the top names by 12-1 momentum (the live momentum engine's ranking) with their
1-month move, daily ATR and sector. Descriptive only -- not a tested signal
beyond what TRIALS #20-#26 and #76 measured for 12-1 momentum.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.features.bars import fetch_many
from scanner.features.technicals import compute
from scanner.sources import composite
from scanner.sources.index_history import SP400, SP500, SP600, IndexSpec, fetch_wikitext

OUT = Path("logs/universes")
NASDAQ_100 = IndexSpec(key="ndx", label="Nasdaq-100", page="List of NASDAQ-100 companies")
#: Not stocks in the sense this desk trades (operator: stocks only, no crypto).
EXCLUDED = {"MSTR"}
MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 20_000_000.0
TOP = 10
TREND_DAYS = 210
#: Rows of the constituents table: "| ADBE || [[Adobe Inc.]] || ...".
_TICKER = re.compile(r"\n\|\s*([A-Z]{1,5}(?:\.[A-Z])?)\s*\|\|")


async def nasdaq_extras(client, in_sp: set[str]) -> dict[str, str]:
    """Nasdaq-100 names outside every S&P index -> 'Nasdaq-100' as the sector label."""
    text = await fetch_wikitext(client, NASDAQ_100)
    table = text[text.find('id="constituents"'):]
    table = table[:table.find("\n|}")]
    names = {t.replace(".", "-") for t in _TICKER.findall(table)}
    if len(names) < 90:
        raise ValueError(f"only {len(names)} Nasdaq-100 names parsed -- the page changed")
    return {s: "Nasdaq-100" for s in sorted(names - in_sp - EXCLUDED)}


def row(symbol: str, bars, sector: str) -> dict | None:
    if bars is None or len(bars) < 260:
        return None
    f = compute(bars)
    closes, vols = list(bars.closes), list(bars.volumes)
    dollar = sum(c * v for c, v in zip(closes[-20:], vols[-20:])) / 20
    return {"symbol": symbol, "sector": sector, "price": f.price, "mom": f.momentum_12_1,
            "m1": closes[-1] / closes[-22] - 1, "atr": f.atr_pct,
            "above": closes[-1] > sum(closes[-TREND_DAYS:]) / TREND_DAYS,
            "liquid": f.price >= MIN_PRICE and dollar >= MIN_DOLLAR_VOLUME}


def section(label: str, rows: list[dict], total: int) -> list[str]:
    ok = [r for r in rows if r["liquid"]]
    ranked = sorted((r for r in ok if r["mom"] is not None), key=lambda r: -r["mom"])[:TOP]
    above = sum(r["above"] for r in ok) / len(ok) * 100 if ok else 0.0
    lines = [f"## {label}", "",
             f"{total} names; {len(rows)} priced; **{len(ok)} clear the floors**; "
             f"{above:.0f}% of those above their {TREND_DAYS}-day average.", "",
             "| # | Stock | Sector | 12-1 momentum | 1 month | Daily ATR |",
             "|---|---|---|---|---|---|"]
    for i, r in enumerate(ranked, 1):
        lines.append(f"| {i} | {r['symbol']} | {r['sector']} | {r['mom']:+.0f}% | "
                     f"{r['m1'] * 100:+.1f}% | {(r['atr'] or 0):.1f}% |")
    mix = Counter(r["sector"] for r in ranked)
    lines += ["", "Top-10 sector mix: " + ", ".join(f"{k} {v}" for k, v in mix.most_common()), ""]
    return lines


async def main() -> int:
    today = date.today()
    async with httpx.AsyncClient(timeout=120) as client:
        universes: list[tuple[str, dict[str, str]]] = []
        seen: set[str] = set()
        for spec, label in ((SP500, "Large -- S&P 500"), (SP400, "Mid -- S&P MidCap 400"),
                            (SP600, "Small -- S&P SmallCap 600")):
            members = {m.symbol: m.sector for m in await composite.load_universe(client, (spec,))}
            universes.append((label, members))
            seen |= set(members)
        try:
            universes.append(("Nasdaq -- Nasdaq-100 names in no S&P index",
                              await nasdaq_extras(client, seen)))
        except Exception as exc:                          # noqa: BLE001
            print(f"  Nasdaq-100 list unavailable: {exc}")
        allsyms = tuple(sorted({s for _, m in universes for s in m}))
        print(f"fetching daily bars for {len(allsyms)} names ...")
        daily, errors = await fetch_many(allsyms, client, range_="2y")
    lines = [f"# Universes {today}", "",
             "Read-only. What each universe would bring; nothing here changes a pool or a trade. "
             f"Floors: price >= ${MIN_PRICE:.0f}, 20-day average daily dollar volume >= "
             f"${MIN_DOLLAR_VOLUME / 1e6:.0f}M. Ranked by 12-1 momentum (the live momentum engine's "
             "measure). MSTR is left out (bitcoin holder; stocks only). The Dow 30 adds no names: "
             "all 30 are in the S&P 500.", ""]
    for label, members in universes:
        rows = [r for s, sec in members.items() if (r := row(s, daily.get(s), sec))]
        lines += section(label, rows, len(members))
    lines.append(f"_{len(errors)} names could not be priced._")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{today.isoformat()}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nsaved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
