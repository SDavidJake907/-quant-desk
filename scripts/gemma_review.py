"""Gemma's evening portfolio review (advisor/gemma_review.py). TRADES NOTHING.

    python scripts/gemma_review.py      # after the close (scheduled 12:30 AK)

1  Her book from the broker: each holding's P/L, weight and room to its live stop.
2  Her stock pool round 1 (the same as the 05:45 run) gives the candidates.
3  Facts for holdings + candidates, her scoreboard, the market -- then she
   thinks it through and writes the review (verdicts, watchlist, events,
   proposals). Saved to data/gemma/reviews/<day>.json and latest.json, which
   the dashboard's Review tab and Ask Gemma read.
"""

from __future__ import annotations

import asyncio
import functools
import json
import sys
from datetime import date, datetime
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import gemma_trade as gtr  # scripts/ is on the path when this script runs

from scanner.advisor import gemma_memory as gm
from scanner.advisor import gemma_review as rv
from scanner.advisor import hindsight_memory
from scanner.advisor import operator_notes
from scanner.advisor import memory_feed
from scanner.advisor import opinions
from scanner.config import settings
from scanner.execution import ownership, picks
from scanner.execution.alpaca import Alpaca
from scanner.execution.ownership import GEMMA
from scanner.execution.scorecard import read_rows
from scanner.features.bars import fetch_bars, fetch_many
from scanner.llm.ollama import LLMError

REVIEWS = Path("data/gemma/reviews")
#: After the close the 05:45 run's day-stamped bar cache lacks today's bar; an
#: hour's TTL makes the review read today's close.
BARS_TTL_S = 3600.0
LIVE = ("new", "held", "accepted", "pending_new")


def live_stops(orders) -> dict[str, float]:
    """The live stop price per symbol, from nested orders (held legs count)."""
    out: dict[str, float] = {}

    def walk(o):
        if o.get("type") in ("stop", "stop_limit") and o.get("status") in LIVE \
                and o.get("side") == "sell" and o.get("stop_price"):
            out.setdefault(o["symbol"], float(o["stop_price"]))
        for leg in o.get("legs") or []:
            walk(leg)

    for o in orders:
        walk(o)
    return out


def save(review: dict) -> Path:
    REVIEWS.mkdir(parents=True, exist_ok=True)
    body = json.dumps(review, indent=1)
    for path in (REVIEWS / f"{review['day']}.json", REVIEWS / "latest.json"):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)
    return REVIEWS / f"{review['day']}.json"


async def main() -> int:
    gtr.fetch_many = functools.partial(fetch_many, ttl_seconds=BARS_TTL_S)
    gtr.fetch_bars = functools.partial(fetch_bars, ttl_seconds=BARS_TTL_S)
    async with httpx.AsyncClient(timeout=120) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        account = await broker.account()
        positions = await broker._get("/v2/positions")
        all_orders = await broker._get("/v2/orders", status="all", limit=500, nested="true")
        owner_map = ownership.owners(all_orders)
        mine = [p for p in positions if ownership.owner(p["symbol"], owner_map) == GEMMA]
        held = [p["symbol"] for p in mine]
        sleeve = account.equity * gtr.POLICY.gemma_allocation_pct / 100
        views = rv.position_views(mine, live_stops(all_orders), sleeve=sleeve,
                                  sell_ifs=gm.sell_ifs(read_rows(settings.audit_log_path)))
        numbers = rv.book_numbers(views, sleeve=sleeve)
        print(f"Gemma's book: {held or 'nothing'}; ${numbers['invested']:,.0f} of "
              f"${sleeve:,.0f} invested ({numbers['invested_pct']:.0f}%)")

        taken = {p["symbol"] for p in positions if p["symbol"] not in held}
        taken |= set(picks.read_list()[0])
        _, mom_picks, _ = opinions.momentum_picks()
        try:
            candidates, pool_size = await gtr.pool_shortlist(client, taken, held, mom_picks)
        except Exception as exc:                          # noqa: BLE001
            candidates, pool_size = [], 0
            print(f"  pool unavailable ({type(exc).__name__}: {exc}) -- no watchlist today")

        facts, trend, _ = await gtr.facts_for([*held, *candidates], client)
        try:
            score = await gtr.scoreboard(client, positions, owner_map, sleeve)
        except Exception as exc:                          # noqa: BLE001
            score = ""
            print(f"  scoreboard unavailable: {exc}")
        memory = await memory_feed.memory_block(client, held=held)
        forecast_score = await memory_feed.forecast_score(client)
        lessons = hindsight_memory.block(hindsight_memory.read_lessons(),
                                         now=datetime.now().astimezone())
        told = operator_notes.block(operator_notes.read(), date.today())
        text = rv.prompt(facts, views, numbers=numbers, scoreboard=score, market=trend,
                         candidates=candidates,
                         memory=(f"{told}\n\n{memory}" if told else memory), lessons=lessons)
        notes = ""
        try:
            answer, notes = await gtr.think_then_answer(text, rv.schema(held, candidates), client)
        except LLMError as exc:
            answer = None
            print(f"  Gemma unavailable ({exc}) -- saving the book without her review")
        review = rv.validate(answer, held=held, candidates=candidates)

    by_symbol = {v.symbol: v for v in views}
    for p in review["positions"]:
        v = by_symbol.get(p["symbol"])
        if v is not None:
            p.update(v.model_dump())
        if not p.get("sell_if"):
            p["sell_if_met"] = "unclear"      # nothing of hers to check
    path = save({**review, "day": date.today().isoformat(),
                 "at": datetime.now().astimezone().isoformat(), "numbers": numbers,
                 "market": trend, "scoreboard": score, "pool_size": pool_size,
                 "candidates": candidates, "thinking": notes, "memory": memory,
                 "forecast_score": forecast_score})
    print(f"\n{review['summary']}")
    for p in review["positions"]:
        print(f"  {p['symbol']:<6} {p['verdict']:<15} sell-if met: {p['sell_if_met']:<8}"
              f"{p['reason'][:100]}")
    for w in review["watchlist"]:
        print(f"  next: {w['symbol']:<6} {w['why'][:70]} | buy if {w['buy_if'][:60]}")
    for pr in review["proposals"]:
        print(f"  proposal: {pr['change']} -- {pr['why'][:90]}")
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
