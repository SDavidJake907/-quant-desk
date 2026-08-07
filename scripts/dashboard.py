"""Build the S&P Composite 1500 heatmap:

    python scripts/dashboard.py                  # all 1,504 names
    python scripts/dashboard.py --index sp500    # one index only
    python scripts/dashboard.py --out board.html

Cells are coloured by relative strength — a stock's 12-1 momentum minus the
benchmark's, the one cross-sectional number the trend screen ranks on. This is
today's state of the universe, not a backtest and not a recommendation.

Membership is TODAY's, which is correct for a live view and would be wrong for
a backtest. See `scripts/backtest.py` for the point-in-time path.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from scanner.features import technicals
from scanner.features.bars import fetch_bars, fetch_many
from scanner.heatmap import CLAMP_PP, NEUTRAL_PP, Cell, build_cells, summarise
from scanner.screens.trend import evaluate
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BENCHMARK = "^GSPC"
DIM, RESET = "\033[2m", "\033[0m"

INDEX_CHOICES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,), "all": COMPOSITE_1500}

TEMPLATE = Path(__file__).with_name("dashboard_template.html")


async def collect(
    client: httpx.AsyncClient, specs, range_: str
) -> tuple[list[Cell], dict, list[str]]:
    universe = await composite.load_universe(client, specs)
    print(f"{DIM}universe: {len(universe)} companies "
          f"across {len({m.index for m in universe})} indices{RESET}")

    benchmark_bars = await fetch_bars(BENCHMARK, client, range_=range_)
    benchmark = technicals.compute(benchmark_bars)

    symbols = tuple(m.symbol for m in universe)
    bars, errors = await fetch_many(symbols, client, range_=range_)
    print(f"{DIM}price history: {len(bars)} loaded, {len(errors)} unavailable{RESET}")

    cells: list[Cell] = []
    for member in universe:
        series = bars.get(member.symbol)
        if series is None:
            continue
        features = technicals.compute(series)
        rs = technicals.relative_strength(features, benchmark)
        candidate = evaluate(features, rs)
        cells.append(
            Cell(
                symbol=member.symbol,
                name=member.name,
                sector=member.sector,
                index=member.index,
                relative_strength=rs,
                momentum_12_1=features.momentum_12_1,
                price=features.price,
                pct_from_52w_high=features.pct_from_52w_high,
                criteria_passed=candidate.passed_count,
                criteria_total=len(candidate.criteria),
            )
        )

    meta = {
        "benchmark_momentum": benchmark.momentum_12_1,
        "missing": len(errors),
        "requested": len(symbols),
    }
    return cells, meta, [e.split(":")[0] for e in errors]


def render(cells: list[Cell], meta: dict, indices: list[str]) -> str:
    blocks = build_cells(cells)
    summary = summarise(blocks)

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "as_of": date.today().isoformat(),
        "indices": indices,
        "clamp": CLAMP_PP,
        "neutral": NEUTRAL_PP,
        "benchmark_momentum": meta["benchmark_momentum"],
        "missing": meta["missing"],
        "requested": meta["requested"],
        "summary": summary,
        "sectors": [
            {
                "sector": block.sector,
                "median": block.median_rs,
                "passing": block.passing,
                "cells": [
                    {
                        "s": c.symbol,
                        "n": c.name,
                        "i": c.index,
                        "rs": c.relative_strength,
                        "m": c.momentum_12_1,
                        "p": c.price,
                        "h": c.pct_from_52w_high,
                        "c": c.criteria_passed,
                        "t": c.criteria_total,
                    }
                    for c in block.cells
                ],
            }
            for block in blocks
        ],
    }

    template = TEMPLATE.read_text(encoding="utf-8")
    return template.replace(
        "/*__DATA__*/null",
        json.dumps(payload, allow_nan=False, default=lambda o: None),
    ).replace("__AS_OF__", html.escape(payload["as_of"]))


async def main(index: str, range_: str, out: Path) -> None:
    specs = INDEX_CHOICES[index]
    async with httpx.AsyncClient() as client:
        cells, meta, _ = await collect(client, specs, range_)

    if not cells:
        raise SystemExit("no cells to render — no price history loaded")

    out.write_text(render(cells, meta, [s.label for s in specs]), encoding="utf-8")
    blocks = build_cells(cells)
    summary = summarise(blocks)
    print(f"\nwrote {out}")
    print(f"  {summary['companies']} companies, {summary['sectors']} sectors, "
          f"{summary['passing']} passing the full trend screen")
    print(f"  {summary['beating_index']} of {summary['rated']} rated names beat the index; "
          f"median RS {summary['median_rs']:+.1f}pp")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--index", default="all", choices=list(INDEX_CHOICES))
    p.add_argument("--range", dest="range_", default="2y",
                   help="history to fetch; 12-1 momentum needs >1y")
    p.add_argument("--out", type=Path, default=Path("dashboard.html"))
    a = p.parse_args()
    asyncio.run(main(a.index, a.range_, a.out))
