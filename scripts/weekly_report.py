"""The Friday weekly review: every open position, every book, against the S&P 500.

    python scripts/weekly_report.py      # print it and save logs/weekly/YYYY-MM-DD.md

Scheduled Fridays 07:00 AK (after Gemma's 05:45 decision, five hours before
the close) so the operator can decide what to hold over the weekend with the
facts in one place. READ-ONLY: it sends no order. See scanner/ops/weekly.py
for what each column means; the trend label describes, it does not predict.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import opinions
from scanner.config import settings
from scanner.execution import ownership
from scanner.execution.alpaca import Alpaca
from scanner.execution.scorecard import read_rows
from scanner.features.bars import fetch_bars, fetch_many
from scanner.ops import weekly as w
from scanner.sources import calendar as cal

OUT = Path("logs/weekly")
LIVE = ("held", "new", "accepted", "pending_new")
BOOK_NAMES = {"picks": "Your picks", "gemma": "Gemma", "manual": "Your one-off trades",
              "momentum": "Momentum (winding down)", "smc": "Sweep scanner (paused)"}


def stops_from(history) -> dict[str, float]:
    out = {}
    for o in history:
        if o.get("side") != "buy" or not o.get("filled_at") or o["symbol"] in out:
            continue
        sl = next((float(l["stop_price"]) for l in o.get("legs") or []
                   if l.get("stop_price") and l.get("status") in LIVE), None)
        if sl:
            out[o["symbol"]] = sl
    return out


def gemma_reasons(rows) -> dict[str, str]:
    for r in reversed(rows):
        if r.get("event") == "gemma_decision":
            try:
                return json.loads(r.get("reason", "{}")).get("reasons", {})
            except ValueError:
                return {}
    return {}


async def gather():
    async with httpx.AsyncClient(timeout=60) as c:
        b = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, c)
        account = await b._get("/v2/account")
        positions = await b._get("/v2/positions")
        history = await b._get("/v2/orders", status="all", limit=500, nested="true")
        symbols = tuple(p["symbol"] for p in positions)
        bars, _ = await fetch_many(symbols, c, range_="6mo") if symbols else ({}, ())
        spx = await fetch_bars("^GSPC", c, range_="6mo")
        try:
            earnings = await cal.upcoming_earnings(frozenset(symbols), c, horizon=10)
        except Exception:                                  # noqa: BLE001
            earnings = {}
        events = {}
        for k in range(1, 8):
            day = date.today() + timedelta(days=k)
            if day.weekday() < 5:
                try:
                    events[day] = await cal.market_moving_events(c, on=day)
                except Exception:                          # noqa: BLE001
                    events[day] = ()
    return account, positions, history, bars, spx, earnings, events


def render(account, positions, history, bars, spx, earnings, events) -> str:
    owners = ownership.owners(history)
    stops = stops_from(history)
    rows = read_rows(settings.audit_log_path)
    reasons = gemma_reasons(rows)
    spx_week = w.week_change(spx.closes)
    lines = [f"# Weekly review {date.today():%A %Y-%m-%d}", "",
             f"Equity **${float(account['equity']):,.0f}**. S&P 500 over the last five sessions "
             f"**{spx_week:+.2f}%**; it is {w.trend_label(spx.closes)}.", ""]
    by = defaultdict(list)
    for p in positions:
        by[ownership.owner(p["symbol"], owners)].append(p)
    for book in ("picks", "gemma", "manual", "momentum", "smc", "unknown"):
        group = by.get(book)
        if not group:
            continue
        cost = sum(float(p["cost_basis"]) for p in group)
        pl = sum(float(p["unrealized_pl"]) for p in group)
        lines += [f"## {BOOK_NAMES.get(book, book)}: {len(group)} positions, "
                  f"{pl:+,.2f} ({w.pct(cost + pl, cost):+.2f}% since entry)", "",
                  "| Stock | Since entry | Stock, last 5 sessions | vs S&P | Room to stop | Trend | Earnings | Advisory |",
                  "|---|---|---|---|---|---|---|---|"]
        for p in sorted(group, key=lambda x: -float(x["unrealized_plpc"])):
            s = p["symbol"]
            closes = bars[s].closes if s in bars else ()
            wk = w.week_change(closes) if closes else 0.0
            room = w.stop_room(float(p["current_price"]), stops.get(s))
            e = earnings.get(s)
            ta = opinions.latest_calls(rows, s).get("tradingagents")
            notes = [f"TA {ta['call']}"] if ta else []
            if book == "gemma" and s in reasons:
                notes.append(f"Gemma: {reasons[s][:70]}")
            lines.append(
                f"| {s} | {float(p['unrealized_plpc']) * 100:+.2f}% | {wk:+.2f}% | {wk - spx_week:+.2f} pts | "
                f"{f'{room:.1f}%' if room is not None else 'no stop'} | {w.trend_label(closes) if closes else '?'} | "
                f"{e.label if e else '-'} | {'; '.join(notes) or '-'} |")
        lines += [""]
    lines += ["## Next week's market calendar", ""]
    lines += [f"- {d:%a %m/%d}: {', '.join(sorted(set(ev))) if ev else 'nothing market-moving'}"
              for d, ev in events.items()]
    lines += ["", "_Trend labels describe where a stock stands (vs its 50-day average and its "
              "month); they were not a tested signal here. Advisory calls are unproven._", ""]
    return "\n".join(lines)


async def main() -> int:
    text = render(*await gather())
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{date.today().isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
