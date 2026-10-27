"""The rules book's 9:45 ET pick (rules in scanner/execution/rules_pick.py). No model is asked.

    python scripts/rules_pick.py            # show what it would queue
    python scripts/rules_pick.py --submit   # queue it for the 10:00 ET buy (peak watch sends it)

Operator 2026-10-26: "stop Gemma from picking, and have the code find the stocks". The S&P 500
through the scanner's floor; the names that pass the plan's buy checks; strongest 12-1 momentum
first into the free slots of the 40% sleeve (6 names). Sizing, stops and exits are the plan's
(execution/plan_run.py). Saves the day's pool for the breakout book (execution/orb.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import gemma_pool as gp  # noqa: E402
from scanner.config import settings  # noqa: E402
from scanner.execution import bookorders, buy_queue, ownership, rules_pick as rp  # noqa: E402
from scanner.execution import satellite as sat, satellite_feed, sessions  # noqa: E402
from scanner.execution.alpaca import Alpaca  # noqa: E402
from scanner.execution.ownership import GEMMA  # noqa: E402
from scanner.execution.policy import TradingPolicy  # noqa: E402
from scanner.execution.safety import kill_switch_active  # noqa: E402
from scanner.features.bars import fetch_many  # noqa: E402
from scanner.features.intraday_context import atr_pct  # noqa: E402
from scanner.features.technicals import compute  # noqa: E402
from scanner.sources import alpaca_bars, composite, market_caps  # noqa: E402
from scanner.sources.index_history import SP500  # noqa: E402

POLICY = TradingPolicy()


async def pool_rows(client: httpx.AsyncClient) -> list[gp.PoolRow]:
    """The S&P 500 through the scanner's floor and the $2B cap floor, one row a name."""
    members = await composite.load_universe(client, (SP500,))
    sectors = {m.symbol: m.sector for m in members}
    caps = await market_caps.load(client)
    names = tuple(s for s in sectors if gp.large_enough(s, caps))
    daily, _ = await fetch_many(names, client, range_="2y")
    bars = {s: b for s, b in daily.items()
            if b is not None and len(b.closes) >= 30 and gp.tradeable(
                b.closes, b.highs, b.lows, b.volumes, min_price=POLICY.min_price,
                min_range_pct=POLICY.min_range_pct, min_dollar_volume=POLICY.min_dollar_volume)}
    return gp.rows_for(bars, sectors, momentum={s: compute(b).momentum_12_1 for s, b in bars.items()},
                       atr={s: atr_pct(b) for s, b in bars.items()}, momentum_rank={})


async def decision_prices(client: httpx.AsyncClient, symbols: list[str]) -> dict[str, float]:
    """Each pick's price now, for the 10:00 recheck (buy_queue.recheck skips a 2% drop)."""
    try:
        snaps = await alpaca_bars.snapshots(symbols, client)
    except Exception as exc:                                  # noqa: BLE001 -- the recheck skips the drop test
        print(f"  no decision prices for the 10:00 recheck ({type(exc).__name__})")
        return {}
    return {s: float(v["latestTrade"]["p"]) for s, v in snaps.items() if (v or {}).get("latestTrade")}


def write_queue(today: date, items: dict[str, dict]) -> None:
    path = Path(buy_queue.PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(buy_queue.make(today, items), indent=1), encoding="utf-8")


async def main(submit: bool) -> int:
    async with httpx.AsyncClient(timeout=120) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        if submit and await sessions.current(broker) != sessions.REGULAR:
            print("not regular hours -- the rules book buys in regular hours only")
            return 0
        if kill_switch_active(settings.kill_switch_path):
            print("kill switch active -- nothing queued")
            return 0
        positions = await broker._get("/v2/positions")
        open_orders = await broker._get("/v2/orders", status="open", nested="true")
        owner_map = ownership.owners(await broker._get("/v2/orders", status="all", limit=500, nested="true"))
        mine = [p["symbol"] for p in positions if ownership.owner(p["symbol"], owner_map) == GEMMA]
        working = {o["symbol"] for o in open_orders if o.get("side") == "buy"
                   and ownership.strategy_of(o.get("client_order_id")) == GEMMA}
        taken = {p["symbol"] for p in positions} | {o["symbol"] for o in open_orders}
        slots = rp.free_slots(max_positions=POLICY.gemma_max_positions, held=len(mine), working=len(working))
        print(f"rules book: holds {sorted(mine) or 'nothing'}; {slots} free of {POLICY.gemma_max_positions}")

        rows = await pool_rows(client)
        if submit:
            gp.save(rows, date.today())                       # the breakout book trades from it
        gates = await satellite_feed.gates(client)
        buyable, _ = await satellite_feed.buyable_pool(client, rows, gates, date.today())
        candidates = rp.ranked(buyable, exclude=taken)
        print(f"  {len(buyable)} of {len(rows)} S&P 500 names pass the buy checks; "
              f"strongest: {[r.symbol for r in candidates[:10]]}")
        if slots == 0 or not candidates:
            print("  nothing to queue")
            return 0
        refused = await satellite_feed.refusals(client, [r.symbol for r in candidates], mine, gates,
                                                settings.audit_log_path)
        picks = rp.choose(candidates, refused=refused, slots=slots)
        for r in candidates:
            if picks and r is picks[-1]:
                break
            if r.symbol in refused:
                print(f"  {r.symbol:<6} skipped -- {refused[r.symbol]}")
        if not picks:
            print("  every candidate refused -- nothing queued")
            return 0
        rank = {r.symbol: i for i, r in enumerate(candidates, 1)}
        items = {r.symbol: rp.queue_item(r, rank=rank[r.symbol], passing=len(candidates)) for r in picks}
        prices = await decision_prices(client, list(items))
        today_et = datetime.now(sat.ET).date()
        items = {s: {**v, "alerts": [], **({"price": prices[s]} if s in prices else {})} for s, v in items.items()}
        for s, v in items.items():
            print(f"  QUEUE {s:<6} {v['reason']}")
        if not submit:
            print("  (dry run -- nothing queued)")
            return 0
        write_queue(today_et, items)
        for s, v in items.items():
            bookorders.log("rules_pick", s, v["reason"], POLICY)
        print(f"  queued for 10:00 ET (peak watch sends them, sized then): {list(items)}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--submit", action="store_true", help="write the 10:00 ET buy queue")
    raise SystemExit(asyncio.run(main(p.parse_args().submit)))
