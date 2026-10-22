"""Peak watch: every 5 minutes in market hours, mark READY and sell before it is too late.

    python scripts/peak_watch.py            # one pass (task stock-scanner-peakwatch, every 5 min)
    python scripts/peak_watch.py --dry-run  # show what it would do, send nothing

Operator 2026-10-22: "the frist one should be mark ready to sell, and befor it to late sell it";
chose "5-min check, auto-sell". For every position in Gemma's book and the operator's picks:
  READY  in profit and >= 4 of the six peak checks (scanner/ops/peak_sell.py) -> marked
  SELL   a marked position whose 5-minute SuperTrend has turned DOWN while still in profit
Gemma's sells go through bookorders.sell (audit log, her memory sees the reason); picks through
picks.txt + picks_sync. Marks: data/monitor/peak_marks.json. Log: logs/peak_watch/DATE.md.
An operator rule, not a tested edge (FREEZE.md).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import gemma_review as grv  # scripts/ is on the path when this script runs
import gemma_trade as gtr

from scanner.config import settings
from scanner.execution import bookorders, ownership
from scanner.execution.alpaca import Alpaca
from scanner.execution.ownership import GEMMA, PICKS
from scanner.features.bars import fetch_many
from scanner.features.intraday import EXCHANGE_TZ
from scanner.features.technicals import average_true_range_pct
from scanner.ops import notify
from scanner.ops import peak_sell as ps
from scanner.signals.candles24 import yahoo_5m

MARKS = Path("data/monitor/peak_marks.json")
OUT = Path("logs/peak_watch")
OPEN, CLOSE = time(9, 30), time(16, 0)


async def readings_for(client, positions, stops: dict, log) -> list[ps.Readings]:
    """Live peak readings (with the ATR stop note) for the given Alpaca positions."""
    if not positions:
        return []
    symbols = tuple(p["symbol"] for p in positions)
    daily, _ = await fetch_many(symbols, client, range_="1y")
    now = datetime.now(EXCHANGE_TZ).replace(tzinfo=None, second=0, microsecond=0)
    out = []
    for p in positions:
        s = p["symbol"]
        try:
            raw = await yahoo_5m(s, client, range_="1mo")
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            log(f"  no 5m candles for {s} ({exc})")
            raw = []
        five = [(t.astimezone(EXCHANGE_TZ).replace(tzinfo=None), *rest) for t, *rest in raw]
        five = [x for x in ps.session(five) if x[0] <= now - timedelta(minutes=5)]     # completed candles only
        price = float(p["current_price"])
        closes = list(daily[s].closes) if s in daily else []
        if closes and daily[s].dates[-1] == now.date():
            closes[-1] = price
        elif closes:
            closes.append(price)
        r = ps.readings(s, float(p["unrealized_plpc"]) * 100, five, closes)
        atr = average_true_range_pct(daily[s]) if s in daily else None
        out.append(dataclasses.replace(r, stop_note=ps.stop_note(price, stops.get(s), atr)))
    return out


def load_marks() -> dict:
    try:
        return json.loads(MARKS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remove_picks(symbols: list[str], why: str) -> list[str]:
    import market_monitor  # same picks.txt edit + commit the monitor uses
    return market_monitor.remove_picks(symbols, why)


async def main(dry: bool) -> int:
    now = datetime.now(EXCHANGE_TZ)
    if now.weekday() > 4 or not (OPEN <= now.time() < CLOSE):
        print(f"{now:%H:%M} ET: market closed -- nothing to watch")
        return 0
    lines: list[str] = []

    def log(text: str) -> None:
        print(text, flush=True)
        lines.append(text)

    log(f"\n## {now:%H:%M} ET{' DRY RUN' if dry else ''}")
    alerts = []
    async with httpx.AsyncClient(timeout=120) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        positions = await broker._get("/v2/positions")
        orders = await broker._get("/v2/orders", status="all", limit=500, nested="true")
        open_orders = await broker._get("/v2/orders", status="open", nested="true")
        owner_map = ownership.owners(orders)
        stops = grv.live_stops(orders)
        book_of = {p["symbol"]: ownership.owner(p["symbol"], owner_map) for p in positions}
        watched = [p for p in positions if book_of[p["symbol"]] in (GEMMA, PICKS)]
        rs = await readings_for(client, watched, stops, log)
        prices = {p["symbol"]: float(p["current_price"]) for p in watched}
        marks, ready, sell = ps.update_marks(load_marks(), rs, now=f"{now:%m-%d %H:%M} ET", prices=prices)
        for r in rs:
            stage = "SELL" if r.symbol in sell else "READY" if r.symbol in marks else "-"
            trend = r.values.get("trend_5m")
            log(f"  {r.symbol:<6} {book_of[r.symbol]:<5} P/L {r.pl_pct:+6.2f}%  {r.score()}/6  5m trend "
                f"{'UP' if trend == 1.0 else 'DOWN' if trend == 0.0 else 'n/a'}  {stage}")
        for s in ready:
            log(f"  READY {s}: in profit with >= {ps.MIN_MET} of 6 peak checks -- sells when the 5m trend turns down")
            alerts.append(f"{s} READY to sell (peak checks)")
        for s in sell:
            m = marks.get(s, {})
            why = (f"peak-sell: READY since {m.get('ready_at')} at {m.get('ready_price')}, "
                   f"the 5-minute trend turned down while in profit")
            if dry:
                log(f"  WOULD SELL {book_of[s]} {s}: {why}")
                continue
            if book_of[s] == PICKS:
                remove_picks([s], why)
                subprocess.run([sys.executable, "scripts/picks_sync.py", "--submit"], check=False)
                ok = True
            else:
                ok = await bookorders.sell(broker, s, open_orders, strategy=GEMMA, reason=why,
                                           policy=gtr.POLICY, client=client)
            log(f"  {'SOLD' if ok else 'SELL FAILED'} {book_of[s]} {s}: {why}")
            if ok:
                alerts.append(f"Sold {s} ({book_of[s]}) -- took profit, trend turned after READY")
                marks.pop(s, None)
        if alerts and not dry:
            body = "; ".join(alerts)
            await notify.send(client, server=settings.ntfy_server, topic=settings.ntfy_topic,
                              title="Quant-desk peak watch", message=body, priority="high", tags=("moneybag",))
            notify.toast("Quant-desk peak watch", body)
    if not dry:
        MARKS.parent.mkdir(parents=True, exist_ok=True)
        MARKS.write_text(json.dumps(marks, indent=1), encoding="utf-8")
        OUT.mkdir(parents=True, exist_ok=True)
        with (OUT / f"{now.date().isoformat()}.md").open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="show what it would do; send nothing")
    raise SystemExit(asyncio.run(main(p.parse_args().dry_run)))
