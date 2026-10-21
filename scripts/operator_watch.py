"""Gemma's safety net over the OPERATOR's positions (advisor/operator_watch.py). TRADES NOTHING.

    python scripts/operator_watch.py      # after her 12:30 AK review, same task

1  The operator's positions (every one Gemma does not own), each with its
   live stop, P/L and room to the stop.
2  Code flags: a report within 7 days, a price within 1 daily ATR of the stop,
   no live stop.
3  The same facts she gets for her own stocks; she gives each ok / watch /
   concern, why, and what to watch. Her prompt never mentions the competition.
4  Saved to data/gemma/operator_watch/<day>.json and into the day's review
   (dashboard). A "concern" pops a Windows notification and pushes ntfy if set.
5  Earlier verdicts GRADE_SESSIONS sessions old are graded vs the S&P 500.
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

import gemma_review as grv  # scripts/ is on the path when this script runs
import gemma_trade as gtr

from scanner.advisor import gemma_review as rv
from scanner.advisor import operator_watch as ow
from scanner.config import settings
from scanner.execution import ownership
from scanner.execution.alpaca import Alpaca
from scanner.execution.ownership import GEMMA
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.technicals import average_true_range_pct
from scanner.llm.ollama import LLMError
from scanner.ops import notify
from scanner.sources import calendar as event_calendar

OUT = Path("data/gemma/operator_watch")


def _excess(bars, spx, day: date, sessions: int) -> float | None:
    """Stock minus S&P 500 return, %, from `day`'s close to `sessions` closes later."""
    i, j = bars.index_of(day), spx.index_of(day)
    if i is None or j is None or bars.dates[i] != day or spx.dates[j] != day:
        return None
    if i + sessions >= len(bars.closes) or j + sessions >= len(spx.closes):
        return None
    stock = bars.closes[i + sessions] / bars.closes[i] - 1
    bench = spx.closes[j + sessions] / spx.closes[j] - 1
    return (stock - bench) * 100


async def grade_history(client: httpx.AsyncClient) -> dict:
    days = sorted(OUT.glob("????-??-??.json"))
    rows = []
    for f in days:
        try:
            saved = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows += [(date.fromisoformat(saved["day"]), r["symbol"], r["verdict"])
                 for r in saved.get("positions", [])]
    if not rows:
        return {}
    bars, _ = await fetch_many(tuple({s for _, s, _ in rows}), client, range_="3mo")
    spx = await fetch_bars("^GSPC", client, range_="3mo")
    scored = []
    for day, s, verdict in rows:
        b = bars.get(s)
        x = _excess(b, spx, day, ow.GRADE_SESSIONS) if b else None
        if x is not None:
            scored.append((verdict, x))
    return ow.grade(scored)


def save(day: str, payload: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{day}.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    # Into the day's review, so the dashboard's review panel shows it.
    for path in (grv.REVIEWS / f"{day}.json", grv.REVIEWS / "latest.json"):
        try:
            review = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if review.get("day") != day:
            continue
        review["operator_book"] = payload
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(review, indent=1), encoding="utf-8")
        tmp.replace(path)


async def main() -> int:
    gtr.fetch_many = functools.partial(fetch_many, ttl_seconds=grv.BARS_TTL_S)
    gtr.fetch_bars = functools.partial(fetch_bars, ttl_seconds=grv.BARS_TTL_S)
    day = date.today().isoformat()
    async with httpx.AsyncClient(timeout=120) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        account = await broker.account()
        positions = await broker._get("/v2/positions")
        orders = await broker._get("/v2/orders", status="all", limit=500, nested="true")
        owner_map = ownership.owners(orders)
        theirs = [p for p in positions if ownership.owner(p["symbol"], owner_map) != GEMMA]
        if not theirs:
            print("the operator holds nothing -- nothing to watch")
            return 0
        views = rv.position_views(theirs, grv.live_stops(orders),
                                  sleeve=account.equity * (100 - gtr.POLICY.gemma_allocation_pct) / 100)
        symbols = [v.symbol for v in views]
        daily, _ = await fetch_many(tuple(symbols), client, range_="3mo")
        try:
            earnings, _ = await event_calendar.upcoming_earnings(
                frozenset(symbols), client, horizon=ow.EARNINGS_DAYS)
        except Exception as exc:                           # noqa: BLE001
            earnings = {}
            print(f"  earnings calendar unavailable: {exc}")
        flags = {v.symbol: ow.code_flags(
                     v, atr_pct=average_true_range_pct(daily[v.symbol]) if v.symbol in daily else None,
                     earnings=earnings[v.symbol].label if v.symbol in earnings else None)
                 for v in views}
        facts, trend, _ = await gtr.facts_for(symbols, client)
        text = ow.prompt(facts, views, flags, market=trend)
        notes = ""
        try:
            answer, notes = await gtr.think_then_answer(text, ow.schema(symbols), client)
        except LLMError as exc:
            answer = None
            print(f"  Gemma unavailable ({exc}) -- saving the code flags only")
        rows = ow.validate(answer, views, flags)
        try:
            graded = await grade_history(client)
        except Exception as exc:                           # noqa: BLE001
            graded = {}
            print(f"  grading unavailable: {exc}")
        save(day, {"day": day, "at": datetime.now().astimezone().isoformat(),
                   "positions": rows, "thinking": notes, "graded": graded})

        print(f"Gemma's safety check of the operator's book ({len(rows)} positions)")
        for r in rows:
            print(f"  {r['symbol']:<6} {r['verdict']:<8} {r['reason'][:110]}")
            if r["watch_for"]:
                print(f"         watch: {r['watch_for'][:110]}")
            for f in r["flags"]:
                print(f"         flag: {f}")
        if graded:
            print("  graded so far (excess vs S&P over "
                  f"{ow.GRADE_SESSIONS} sessions): {graded}")
        bad = ow.concerns(rows)
        if bad:
            title = "Gemma: concern on your positions"
            body = "; ".join(bad)
            await notify.send(client, server=settings.ntfy_server, topic=settings.ntfy_topic,
                              title=title, message=body, priority="high", tags=("eyes",))
            notify.toast(title, body)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
