"""The daily brief: calendar, news, the President, and which stocks it touches.

    python scripts/week_ahead.py            # write today's brief
    python scripts/week_ahead.py --no-llm   # calendar + news only, no links

03:00 AK daily (stock-scanner-weekahead), before TradingAgents takes the GPU at
03:30 and well before Gemma's 05:45 decision, which reads what this writes:
  data/brief/YYYY-MM-DD.json   for Gemma (scripts/gemma_trade.py)
  logs/week_ahead/YYYY-MM-DD.md   for the operator

Places no order and touches no policy. WORKLIST §52.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import news_impact as ni
from scanner.advisor import opinions
from scanner.advisor.scan_watch import operator_names
from scanner.config import settings
from scanner.execution import ownership, picks
from scanner.execution.alpaca import Alpaca
from scanner.llm.ollama import LLMError, chat_json
from scanner.ops import week_ahead as wa
from scanner.sources import calendar as cal
from scanner.sources import composite, president, yahoo_rss
from scanner.sources.index_history import SP400, SP500
from scanner.sources.market_calendar import parse_sessions

BRIEF_DIR = Path("data/brief")
REPORT_DIR = Path("logs/week_ahead")
LISTS = (Path("watchlists/core.txt"), Path("watchlists/gemma.txt"))
SESSIONS_AHEAD = 10
MARKET_FEEDS = ("^GSPC", "CL=F", "^TNX")     # S&P, crude oil, 10-year: Yahoo's market news
PER_HELD = 3
HEADLINE_HOURS = 48


async def _sessions(broker, today: date) -> list[date]:
    try:
        rows = await broker._get("/v2/calendar", start=today.isoformat(),
                                 end=(today + timedelta(days=21)).isoformat())
        days = [s.on for s in parse_sessions(rows)]
    except Exception:                                      # noqa: BLE001
        days = [today + timedelta(days=k) for k in range(21) if (today + timedelta(days=k)).weekday() < 5]
    return days[:SESSIONS_AHEAD]


async def _headlines(client, symbols) -> list[str]:
    seen, out = set(), []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HEADLINE_HOURS)
    for s in symbols:
        try:
            items = await yahoo_rss.fetch(s, client)
        except Exception:                                  # noqa: BLE001
            continue
        # Yahoo mixes in older articles: keep only what was published recently.
        fresh = [i for i in items if i.published_at and i.published_at >= cutoff]
        for item in fresh[: (8 if s in MARKET_FEEDS else PER_HELD)]:
            if item.title.lower() not in seen:
                seen.add(item.title.lower())
                out.append(item.title)
    return out


async def main(use_llm: bool) -> int:
    today = date.today()
    async with httpx.AsyncClient(timeout=60) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        positions = await broker._get("/v2/positions")
        owner_map = ownership.owners(await broker._get("/v2/orders", status="all", limit=500,
                                                       nested="true"))
        held = {p["symbol"]: ownership.owner(p["symbol"], owner_map) or "?" for p in positions}
        names = list(dict.fromkeys((*held, *picks.read_list()[0], *operator_names(LISTS[0]),
                                    *operator_names(LISTS[1]), *opinions.momentum_picks()[1])))
        members = await composite.load_universe(client, (SP500, SP400))
        sector = {m.symbol: m.sector for m in members}
        universe = {s: sector.get(s, "sector unknown") for s in names}

        days = await _sessions(broker, today)
        econ = {d: await cal.market_moving_events(client, on=d) for d in days}
        earnings = await cal.upcoming_earnings(frozenset(names), client, horizon=16)
        evts = wa.events(days, econ=econ, earnings=earnings)
        posts = await president.posts(client)
        sched = await president.schedule(client, days={today, today + timedelta(days=1)})
        market = await _headlines(client, (*MARKET_FEEDS, *held))

        heads = {f"P{i}": p.text for i, p in enumerate(posts[:10], 1)}
        heads |= {f"H{i}": h for i, h in enumerate(market[:40], 1)}
        sectors = sorted({v for v in universe.values() if v != "sector unknown"})
        impacts: tuple[ni.Impact, ...] = ()
        if use_llm and heads:
            try:
                answer = await chat_json(ni.prompt(heads, universe),
                                         ni.schema(heads, list(universe), sectors), client)
                impacts = ni.validate(answer, heads, list(universe), sectors)
            except LLMError as exc:
                # Upper case on purpose: the desk journal's error check looks for it.
                print(f"FAILED: news links unavailable ({exc}) -- calendar and news still written")

    linked = {i.headline for i in impacts}
    pres_lines = [p.text for p in posts[:10] if p.text in linked]
    pres_lines += [f"schedule {s.day:%a} {s.time}: {s.details}" for s in sched
                   if s.day == today and "Executive Time" not in s.details][:4]
    wide = [i.headline for i in impacts if not i.symbols and i.headline not in pres_lines]
    market_text = wa.market_text(evts, president_lines=pres_lines,
                                 headlines=wide + [h for h in market if h in linked and h not in wide])
    brief = {
        "date": today.isoformat(), "written_at": datetime.now(timezone.utc).isoformat(),
        "market_text": market_text,
        "stock_lines": {s: lines for s in universe if (lines := wa.stock_lines(s, evts, impacts))},
        "flags": wa.flags(evts, held=held),
        "events": [e.model_dump(mode="json") for e in evts],
        "impacts": [i.model_dump(mode="json") for i in impacts],
    }
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    (BRIEF_DIR / f"{today.isoformat()}.json").write_text(json.dumps(brief, indent=1), encoding="utf-8")
    posts_md = [f"{p.when:%a %H:%M UTC} [{p.source}] {p.text}" for p in posts[:12]]
    sched_md = [f"{s.day:%a %m/%d} {s.time}: {s.details}" for s in sched]
    md = wa.render_md(today, evts, brief["flags"], posts_md, sched_md, impacts,
                      [h for h in market if h not in linked][:15])
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{today.isoformat()}.md"
    path.write_text(md, encoding="utf-8")
    # Only a summary goes to the task log: quoted posts in capitals ("FAILED") would
    # trip the desk journal's error check. The full brief is in its own file.
    for f in brief["flags"]:
        print(f"  watch: {f}")
    for i in impacts:
        print(f"  link: {', '.join(i.symbols) or ', '.join(i.sectors)} <- {i.headline[:90]}")
    print(f"saved {path} and {BRIEF_DIR / (today.isoformat() + '.json')}: {len(evts)} events, "
          f"{len(posts)} presidential items, {len(market)} headlines, {len(impacts)} links")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--no-llm", action="store_true", help="skip the news-to-stocks links")
    raise SystemExit(asyncio.run(main(not p.parse_args().no_llm)))
