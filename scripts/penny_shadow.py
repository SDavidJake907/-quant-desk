"""The penny watch-list shadow, daily after the close (rules in scanner/ops/penny_shadow.py).

    python scripts/penny_shadow.py           # record today's would-buys and grade the old ones

NEVER ORDERS: no broker write is made here. Operator 2026-10-27: "keep it, running as a shadow..
no trades". Records to data/shadow/penny.jsonl (one line a day, append-only) and writes the
running grade vs IWM to logs/shadow/penny.md. Runs in the outcomes task (13:30 AK).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings  # noqa: E402
from scanner.execution import satellite as sat  # noqa: E402
from scanner.execution.alpaca import Alpaca  # noqa: E402
from scanner.features.bars import fetch_bars, fetch_many  # noqa: E402
from scanner.features.technicals import compute  # noqa: E402
from scanner.ops import penny_shadow as ps  # noqa: E402
from scanner.sources import alpaca_bars, yahoo_fundamentals  # noqa: E402

RECORDS = Path("data/shadow/penny.jsonl")
REPORT = Path("logs/shadow/penny.md")
CORPORATE_ACTIONS = "https://data.alpaca.markets/v1/corporate-actions"


async def reverse_splits(client: httpx.AsyncClient, symbols: list[str], today: date) -> set[str] | None:
    """Symbols with a reverse split in the last year (Alpaca corporate actions), or None if unreadable."""
    out: set[str] = set()
    try:
        for i in range(0, len(symbols), 100):
            token = None
            while True:
                params = {"symbols": ",".join(symbols[i:i + 100]), "types": "reverse_split",
                          "start": (today - timedelta(days=365)).isoformat(), "end": today.isoformat(),
                          "limit": 1000, **({"page_token": token} if token else {})}
                r = await client.get(CORPORATE_ACTIONS, headers=alpaca_bars._headers(), params=params, timeout=30)
                r.raise_for_status()
                body = r.json() or {}
                for row in (body.get("corporate_actions") or {}).get("reverse_splits") or []:
                    out.add(str(row.get("symbol")))
                token = body.get("next_page_token")
                if not token:
                    break
    except (httpx.HTTPError, ValueError) as exc:
        print(f"  reverse-split check unavailable ({type(exc).__name__}) -- recorded as unchecked")
        return None
    return out


async def record_today(client: httpx.AsyncClient, today: date) -> dict:
    broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
    iwm = await fetch_bars("IWM", client, range_="2y")
    gate = sat.gate_open(iwm.closes)
    record = {"day": today.isoformat(), "gate": gate, "iwm_close": iwm.closes[-1], "picks": []}
    if gate is not True:
        print("penny shadow: IWM gate closed -- no would-buys today")
        return record
    assets = [a for a in await broker._get("/v2/assets", status="active", asset_class="us_equity")
              if ps.is_common_stock(a)]
    snaps = await alpaca_bars.snapshots([a["symbol"] for a in assets], client)
    # Price only: the free (IEX) snapshot volume is a small slice of the real tape, so the
    # dollar-volume floor is checked on full daily bars below.
    band = [s for s, v in snaps.items() if ps.in_band(((v or {}).get("dailyBar") or {}).get("c"))]
    bars, _ = await fetch_many(tuple(band), client, range_="2y")
    trend_ok = {s: b for s, b in bars.items() if b is not None and ps.passes(
        b.closes, b.volumes, operating_margin=1.0, reverse_split=False) is None}
    funds = await yahoo_fundamentals.fetch_many(trend_ok)
    splits = await reverse_splits(client, sorted(trend_ok), today)
    passing = {s: b for s, b in trend_ok.items() if ps.passes(
        b.closes, b.volumes, operating_margin=getattr(funds.get(s), "operating_margin", None),
        reverse_split=bool(splits and s in splits)) is None}
    picks = ps.top({s: compute(b).momentum_12_1 for s, b in passing.items()})
    print(f"penny shadow: {len(assets)} listed common stocks; {len(band)} at $1-$5; "
          f"{len(trend_ok)} trending; {len(passing)} also profitable, no reverse split")
    record.update(listed=len(assets), in_band=len(band), passing=len(passing),
                  splits_checked=splits is not None,
                  picks=[{"symbol": s, "close": passing[s].closes[-1],
                          "mom_12_1": compute(passing[s]).momentum_12_1} for s in picks])
    for p in record["picks"]:
        print(f"  WOULD BUY {p['symbol']:<6} ${p['close']:.2f}  12-1 {p['mom_12_1']:+.0f}%")
    return record


async def grade(client: httpx.AsyncClient, records: list[dict]) -> list[str]:
    """One line per record and horizon that has its sessions: picks' average minus IWM, in points."""
    iwm = await fetch_bars("IWM", client, range_="2y")
    symbols = tuple({p["symbol"] for r in records for p in r.get("picks") or []})
    bars, _ = await fetch_many(symbols, client, range_="2y") if symbols else ({}, ())
    lines, by_h = [], {h: [] for h in ps.HORIZONS}
    for r in records:
        day = date.fromisoformat(r["day"])
        after = [d for d in iwm.dates if d > day]
        for h in ps.HORIZONS:
            if len(after) < h:
                continue
            on = after[h - 1]
            closes = {"IWM": iwm.closes[iwm.dates.index(on)]}
            for s in {p["symbol"] for p in r.get("picks") or []}:
                b = bars.get(s)
                if b is not None and on in b.dates:
                    closes[s] = b.closes[b.dates.index(on)]
            x = ps.excess(r, closes)
            if x is not None:
                by_h[h].append(x)
                lines.append(f"| {r['day']} | {h} | {', '.join(p['symbol'] for p in r['picks'])} | {x:+.2f} |")
    summary = [f"- {h} sessions: {len(v)} graded, average {sum(v) / len(v):+.2f} pts vs IWM, "
               f"ahead on {sum(x > 0 for x in v)}" if v else f"- {h} sessions: none graded yet"
               for h, v in by_h.items()]
    return summary + ["", "| day | sessions | would-buys | vs IWM (pts) |", "|---|---|---|---|", *lines]


async def main() -> int:
    today = date.today()
    records = []
    if RECORDS.exists():
        records = [json.loads(x) for x in RECORDS.read_text(encoding="utf-8").splitlines() if x.strip()]
    async with httpx.AsyncClient(timeout=60) as client:
        if not any(r["day"] == today.isoformat() for r in records):
            record = await record_today(client, today)
            RECORDS.parent.mkdir(parents=True, exist_ok=True)
            with RECORDS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            records.append(record)
        body = await grade(client, records)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("# Penny watch-list shadow (never trades)\n\n$1-$5 listed, $5M+/day, trending, "
                      "profitable, no reverse split, IWM gate; 4 strongest 12-1. Graded vs IWM.\n\n"
                      + "\n".join(body) + "\n", encoding="utf-8")
    print("\n".join(body[:len(ps.HORIZONS)]))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
