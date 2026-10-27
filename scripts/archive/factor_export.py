"""Export a point-in-time price panel for the GPU research environment.

    python scripts/factor_export.py                 # both panels
    python scripts/factor_export.py --only LIVE

Runs in THIS project's venv (it needs scanner: index history, Yahoo bars).
Writes data/factors/panel_<NAME>.pkl, read by research_gpu/ in the separate
GPU interpreter (torch/cupy, Microsoft Store Python 3.12) -- the same split as
TradingAgents: the two environments share files, never packages, so the
trading venv stays exactly as frozen.

PANELS
  LIVE   S&P 500 + MidCap 400 point-in-time union, daily, 2014-10 .. now
  CRASH  S&P 500 point-in-time, daily, 1996-10 .. 2016-06
Contents (no numpy here: stdlib arrays of float64, NaN where missing):
  dates       benchmark (^GSPC) trading days
  symbols     every symbol ever a member in the window and priced
  open/high/low/close/volume   T x N, row-major array('d')
  bench       ^GSPC closes
  members     {month-end row index: [symbol column indices in the index then]}
Adjusted prices (Yahoo `adjclose` scale for all four), as everywhere else here.
Survivorship: delisted members have no bars and are absent -- every result
from this panel reads high, as before.
"""

from __future__ import annotations

import argparse
import asyncio
import pickle
import sys
from array import array
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.features.bars import fetch_bars, fetch_many
from scanner.sources import index_history
from scanner.sources.index_history import SP400, SP500

sys.path.insert(0, "scripts")
from momentum_guard_test import union_membership, window  # noqa: E402

from datetime import date

OUT = Path("data/factors")
PANELS = {"LIVE": ((SP500, SP400), date(2014, 10, 1), date(2026, 10, 2), "12y"),
          "CRASH": ((SP500,), date(1996, 10, 1), date(2016, 6, 30), "30y")}
NAN = float("nan")


async def build(name: str) -> Path:
    specs, lo, hi, range_ = PANELS[name]
    async with httpx.AsyncClient(timeout=60) as client:
        histories = [await index_history.load(client, spec=s) for s in specs]
        symbols = tuple(sorted(set().union(*(h.symbols_ever(lo, hi, collapse_share_classes=True)
                                             for h in histories))))
        bench = window(await fetch_bars("^GSPC", client, range_=range_), lo, hi)
        raw, errors = await fetch_many(symbols, client, range_=range_)
    bars = {s: w for s, b in raw.items() if (w := window(b, lo, hi)) is not None and len(w) > 60}
    cols = sorted(bars)
    row = {d: i for i, d in enumerate(bench.dates)}
    t, n = len(bench.dates), len(cols)
    fields = {k: array("d", [NAN]) * (t * n) for k in ("open", "high", "low", "close", "volume")}
    for j, s in enumerate(cols):
        b = bars[s]
        for d, o, h, lo_, c, v in zip(b.dates, b.opens, b.highs, b.lows, b.closes, b.volumes):
            i = row.get(d)
            if i is None:
                continue
            k = i * n + j
            fields["open"][k], fields["high"][k], fields["low"][k] = o, h, lo_
            fields["close"][k], fields["volume"][k] = c, v
    members_on = union_membership(histories)
    col = {s: j for j, s in enumerate(cols)}
    members = {}
    for i in range(t - 1):
        if bench.dates[i + 1].month != bench.dates[i].month:
            members[i] = sorted(col[s] for s in members_on(bench.dates[i]) if s in col)
    panel = {"name": name, "dates": [d.isoformat() for d in bench.dates], "symbols": cols,
             "bench": array("d", bench.closes), "members": members, **fields}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"panel_{name}.pkl"
    path.write_bytes(pickle.dumps(panel, protocol=pickle.HIGHEST_PROTOCOL))
    print(f"{name}: {t} days x {n} symbols ({len(symbols)} ever members, {len(errors)} fetch errors), "
          f"{len(members)} month-ends -> {path} ({path.stat().st_size / 1e6:.0f} MB)")
    return path


async def main(only: str | None) -> int:
    for name in PANELS:
        if only is None or only == name:
            await build(name)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", choices=list(PANELS))
    raise SystemExit(asyncio.run(main(p.parse_args().only)))
