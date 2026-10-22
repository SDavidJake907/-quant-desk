"""Market-hours monitor (scanner/ops/market_monitor.py): alarms, Gemma's checks, protective sells.

    python scripts/market_monitor.py            # one pass (the task runs this every 30 min)
    python scripts/market_monitor.py --dry-run  # show what it would do, send nothing

Every pass in regular hours (task stock-scanner-monitor, :15 and :45 past the hour AK):
  1  MARKET alarm: SPY or QQQ <= -1.5% vs the prior close
  2  POSITION alarms: a holding <= -3% today or within 1 daily ATR of its stop
  3  operator's picks: on a market alarm, sell every picks position held before the
     alarm started today; on a position alarm (or the hourly pass), run Gemma's safety
     check now and sell any picks position she calls a "concern"
  4  Gemma's book, hourly (the :45 pass): she re-reads her own sell-ifs against live
     facts; a met sell-if with a quote found in the facts is sold
  5  hourly: the PEAK-SELL readings and stage of every position (scanner/ops/peak_sell.py,
     operator 2026-10-22), shown to Gemma in her sell-if check -- information only; the
     5-minute peak watch (scripts/peak_watch.py) marks READY and does the selling
Picks sells go through picks.txt + picks_sync (the list stays the record); Gemma's
through bookorders.sell. Everything is logged (logs/monitor/DATE.md, the audit log)
and alerted (Windows toast; phone when SCANNER_NTFY_TOPIC is set).

24/5 (operator 2026-10-22): outside regular hours (premarket, after-hours, overnight) only
step 4 runs, on the :45 pass, and her sells go out as limit orders. Steps 1-3 stay
regular-hours only: their Yahoo quotes are the regular session's, frozen at the close.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import gemma_review as grv  # scripts/ is on the path when this script runs
import gemma_trade as gtr

from scanner.advisor import gemma_memory as gm
from scanner.config import settings
from scanner.execution import bookorders, ownership, sessions
from scanner.execution.alpaca import Alpaca
from scanner.execution.ownership import GEMMA, PICKS
from scanner.execution.scorecard import read_rows
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import EXCHANGE_TZ
from scanner.features.technicals import average_true_range_pct
from scanner.llm.ollama import LLMError
from scanner.ops import market_monitor as mm
from scanner.ops import notify
from scanner.ops import peak_sell as ps

STATE = Path("data/monitor/state.json")
OUT = Path("logs/monitor")
PICKS_FILE = Path("watchlists/picks.txt")
WATCH_FILE = Path("data/gemma/operator_watch")
INDEXES = ("SPY", "QQQ")


def log(lines: list[str], text: str) -> None:
    print(text, flush=True)
    lines.append(text)


async def quote(client, symbol: str) -> tuple[float | None, float | None]:
    """(live price, day change %) from Yahoo's consolidated chart."""
    try:
        r = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                             params={"range": "1d", "interval": "5m"}, headers={"User-Agent": "Mozilla/5.0"})
        meta = r.json()["chart"]["result"][0]["meta"]
        px, prev = float(meta["regularMarketPrice"]), float(meta.get("chartPreviousClose") or meta["previousClose"])
        return px, (px / prev - 1) * 100
    except Exception:                                      # noqa: BLE001 -- one quote, not the run
        return None, None


def load_state(today: date) -> dict:
    try:
        s = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    return s if s.get("day") == today.isoformat() else {"day": today.isoformat(), "alarm_started": None}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1), encoding="utf-8")


def entry_times(orders, strategy: str) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    for o in orders:
        if o.get("side") == "buy" and o.get("filled_at") and ownership.strategy_of(o.get("client_order_id")) == strategy:
            t = datetime.fromisoformat(str(o["filled_at"]).replace("Z", "+00:00"))
            if o["symbol"] not in out or t > out[o["symbol"]]:
                out[o["symbol"]] = t
    return out


def remove_picks(symbols: list[str], why: str) -> list[str]:
    raw = PICKS_FILE.read_bytes().decode("utf-8")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    out, gone = [], []
    for line in raw.split("\n"):
        s = line.strip("\r").strip()
        if s in symbols and s not in gone:
            out.append(f"# {s} removed {stamp} AK by the market monitor ({why})" + ("\r" if line.endswith("\r") else ""))
            gone.append(s)
        else:
            out.append(line)
    if gone:
        PICKS_FILE.write_bytes("\n".join(out).encode("utf-8"))
        subprocess.run(["git", "add", str(PICKS_FILE)], check=False)
        subprocess.run(["git", "commit", "-q", "-m", f"chore: picks -- {', '.join(gone)} sold by the market monitor ({why[:80]})"],
                       check=False)
    return gone


def run_safety_check() -> dict[str, str]:
    """Run Gemma's safety check now; return {symbol: reason} for her concerns from THIS run."""
    started = datetime.now().astimezone()
    subprocess.run([sys.executable, "scripts/operator_watch.py"], check=False)
    try:
        saved = json.loads((WATCH_FILE / f"{date.today().isoformat()}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if datetime.fromisoformat(saved["at"]) < started:
        return {}
    return {r["symbol"]: r["reason"] for r in saved.get("positions", []) if r.get("verdict") == "concern"}


async def gemma_sell_ifs(client, broker, positions, owner_map, open_orders, lines, dry: bool,
                         alerts: list[str], peak: str = "", session: str = sessions.REGULAR) -> list[str]:
    mine = [p for p in positions if ownership.owner(p["symbol"], owner_map) == GEMMA]
    if not mine:
        log(lines, "  Gemma holds nothing")
        return []
    sell_ifs = gm.sell_ifs(read_rows(settings.audit_log_path))
    symbols = [p["symbol"] for p in mine]
    facts, trend, _ = await gtr.facts_for(symbols, client)
    rows = []
    for p in mine:
        _, day = await quote(client, p["symbol"])
        rows.append((p["symbol"], sell_ifs.get(p["symbol"], ""), float(p["unrealized_plpc"]) * 100, day))
    text = mm.sell_if_prompt(facts, rows, market=trend, alerts=alerts, peak=peak)
    try:
        answer, _ = await gtr.think_then_answer(text, mm.sell_if_schema(symbols), client)
    except LLMError as exc:
        log(lines, f"  Gemma unavailable ({exc}) -- her book unchanged this hour")
        return []
    for h in (answer or {}).get("holdings", []):
        log(lines, f"  Gemma {h.get('symbol'):<6} sell-if met: {h.get('met'):<8} {' | '.join(h.get('quotes') or [])[:120]}")
    sold = []
    approved = mm.sell_if_sells(answer, sell_ifs, facts)
    for h in (answer or {}).get("holdings", []):
        if h.get("met") == "yes" and h.get("symbol") not in {s for s, _ in approved}:
            log(lines, f"  kept Gemma {h.get('symbol')}: she said met, but the evidence does not cover every part of "
                       f"her sell-if ('{sell_ifs.get(h.get('symbol'), '')}') or is not in the facts")
    for s, q in approved:
        reason = f"her sell-if met intraday: {q[:150]}"
        if dry:
            log(lines, f"  WOULD SELL Gemma {s}: {reason}")
            continue
        ok = await bookorders.sell(broker, s, open_orders, strategy=GEMMA, reason=reason, policy=gtr.POLICY,
                                   session=session, client=client)
        log(lines, f"  {'SOLD' if ok else 'SELL FAILED'} Gemma {s}: {reason}")
        sold.append(s)
    return sold


async def peak_status(client, positions, owner_map, stops, lines, book: str,
                      session: str = sessions.REGULAR) -> str:
    """Hourly: log every position's peak readings and stage (READY marks come from the 5-minute
    peak watch, scripts/peak_watch.py, which also does the selling). Returns the text Gemma is shown."""
    import peak_watch
    mine = [p for p in positions if ownership.owner(p["symbol"], owner_map) == book]
    if not mine:
        return ""
    rs = await peak_watch.readings_for(client, mine, stops, lambda s: log(lines, s), session)
    marks = peak_watch.load_marks()
    who = "Gemma" if book == GEMMA else "picks"
    for r in rs:
        log(lines, f"  peak ({who}) " + ps.table(r).replace("\n", "\n  "))
    return ps.status_text(rs, marks)


def write_log(now: datetime, lines: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / f"{now.date().isoformat()}.md").open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


async def extended_pass(dry: bool, now: datetime, session: str) -> int:
    """Outside regular hours: Gemma's sell-if check on her own book, sells as limit orders."""
    lines: list[str] = []
    log(lines, f"\n## {now:%H:%M} ET [{session}] (hourly){' DRY RUN' if dry else ''}")
    async with httpx.AsyncClient(timeout=120) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        positions = await broker._get("/v2/positions")
        orders = await broker._get("/v2/orders", status="all", limit=500, nested="true")
        open_orders = await broker._get("/v2/orders", status="open", nested="true")
        owner_map = ownership.owners(orders)
        stops = grv.live_stops(orders)
        peak = await peak_status(client, positions, owner_map, stops, lines, GEMMA, session)
        sold = await gemma_sell_ifs(client, broker, positions, owner_map, open_orders, lines, dry, [],
                                    peak, session)
        if sold and not dry:
            body = f"Gemma sold {', '.join(sold)} ({session}, her sell-if was met)"
            await notify.send(client, server=settings.ntfy_server, topic=settings.ntfy_topic,
                              title="Quant-desk monitor", message=body, priority="high", tags=("warning",))
            notify.toast("Quant-desk monitor", body)
    if not dry:
        write_log(now, lines)
    return 0


async def main(dry: bool) -> int:
    gtr.fetch_many = functools.partial(fetch_many, ttl_seconds=grv.BARS_TTL_S)
    gtr.fetch_bars = functools.partial(fetch_bars, ttl_seconds=grv.BARS_TTL_S)
    now = datetime.now(EXCHANGE_TZ)
    async with httpx.AsyncClient(timeout=60) as client:
        session = await sessions.current(Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client))
    if session == sessions.CLOSED:
        print(f"{now:%H:%M} ET: market closed -- nothing to monitor")
        return 0
    hourly = datetime.now().minute >= 40            # the :45 AK pass also runs the hourly checks
    if session != sessions.REGULAR:
        if not hourly:
            print(f"{now:%H:%M} ET [{session}]: outside regular hours only the :45 pass runs")
            return 0
        return await extended_pass(dry, now, session)
    state = load_state(now.date())
    lines: list[str] = []
    log(lines, f"\n## {now:%H:%M} ET{' (hourly)' if hourly else ''}{' DRY RUN' if dry else ''}")
    alerts: list[str] = []
    async with httpx.AsyncClient(timeout=120) as client:
        idx = {s: (await quote(client, s))[1] for s in INDEXES}
        reason = mm.market_alarm(idx)
        log(lines, "  market: " + ", ".join(f"{k} {v:+.2f}%" if v is not None else f"{k} n/a" for k, v in idx.items())
            + (f" -- MARKET ALARM ({reason})" if reason else ""))
        if reason and not state["alarm_started"]:
            state["alarm_started"] = datetime.now().astimezone().isoformat()
            alerts.append(f"Market alarm: {reason}")
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        positions = await broker._get("/v2/positions")
        orders = await broker._get("/v2/orders", status="all", limit=500, nested="true")
        open_orders = await broker._get("/v2/orders", status="open", nested="true")
        owner_map = ownership.owners(orders)
        stops = grv.live_stops(orders)
        daily, _ = await fetch_many(tuple(p["symbol"] for p in positions), client, range_="3mo")
        entered = entry_times(orders, PICKS)
        books: dict[str, list[mm.Holding]] = {"picks": [], "gemma": [], "other": []}
        for p in positions:
            s = p["symbol"]
            px, day = await quote(client, s)
            hold = mm.Holding(symbol=s, day_change_pct=day, price=px or float(p["current_price"]), stop=stops.get(s),
                              atr_pct=average_true_range_pct(daily[s]) if s in daily else None, entered_at=entered.get(s))
            who = ownership.owner(s, owner_map)
            books["gemma" if who == GEMMA else "picks" if who == PICKS else "other"].append(hold)
        for book, hs in books.items():
            for hold in hs:
                a = mm.position_alarm(hold)
                if a:
                    log(lines, f"  position alarm ({book}): {a}")
                    alerts.append(f"{book}: {a}")
        # The operator's picks: market alarm, or Gemma's concern.
        concerns: dict[str, str] = {}
        picks_alarm = any(mm.position_alarm(h) for h in books["picks"])
        if books["picks"] and (picks_alarm or hourly) and not reason:
            concerns = run_safety_check() if not dry else {}
        started = datetime.fromisoformat(state["alarm_started"]) if state["alarm_started"] else None
        to_sell = mm.picks_to_sell(books["picks"], alarm_started=started, market_reason=reason, concerns=concerns)
        if to_sell:
            for s, why in to_sell:
                log(lines, f"  {'WOULD SELL' if dry else 'SELLING'} picks {s}: {why}")
            if not dry:
                gone = remove_picks([s for s, _ in to_sell], to_sell[0][1])
                subprocess.run([sys.executable, "scripts/picks_sync.py", "--submit"], check=False)
                alerts.append(f"Sold your picks {', '.join(gone)}: {to_sell[0][1]}")
        # Gemma's own book: her sell-ifs once an hour, AND whenever a NEW alarm touches her book
        # (operator 2026-10-22: "lets have the alert go to Gemma") -- each alarm wakes her once a day.
        hers = [mm.position_alarm(h) for h in books["gemma"]]
        gemma_alerts = ([f"market: {reason}"] if reason else []) + [a for a in hers if a]
        told = set(state.get("told_gemma", []))
        new = [a.split(" ")[0] if not a.startswith("market") else "market" for a in gemma_alerts]
        fresh = [k for k in new if k not in told]
        sold: list[str] = []
        if books["gemma"] and (hourly or fresh):
            if fresh and not hourly:
                log(lines, f"  waking Gemma for new alarms: {', '.join(fresh)}")
            peak = await peak_status(client, positions, owner_map, stops, lines, GEMMA) if books["gemma"] and hourly else ""
            sold = await gemma_sell_ifs(client, broker, positions, owner_map, open_orders, lines, dry, gemma_alerts, peak)
            state["told_gemma"] = sorted(told | set(new))
            if sold:
                alerts.append(f"Gemma sold {', '.join(sold)} (her sell-if was met)")
        if books["picks"] and hourly:
            await peak_status(client, positions, owner_map, stops, lines, PICKS)
        for a in [x for x in books["other"]]:
            log(lines, f"  note: {a.symbol} is held outside both books (hand trade) -- alerts only")
        if alerts and not dry:
            body = "; ".join(alerts)
            await notify.send(client, server=settings.ntfy_server, topic=settings.ntfy_topic,
                              title="Quant-desk monitor", message=body, priority="high", tags=("warning",))
            notify.toast("Quant-desk monitor", body)
    if not dry:
        save_state(state)
        write_log(now, lines)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="show what it would do; send nothing")
    raise SystemExit(asyncio.run(main(p.parse_args().dry_run)))
