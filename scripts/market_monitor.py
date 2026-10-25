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
  4b 3:15 PM ET, only under a bellwether / theme-shock alert: her PRE-CLOSE risk check with
     read-only lookups (ops/pre_close.py) -- keep or sell each holding before the close
  5  hourly: the PEAK-SELL readings and stage of every position (scanner/ops/peak_sell.py,
     operator 2026-10-22), shown to Gemma in her sell-if check -- information only; the
     5-minute peak watch (scripts/peak_watch.py) marks READY and does the selling
Sells are QUEUED (execution/sell_queue.py) and sent by peak watch within 5 minutes --
one automatic seller (2026-10-23). Everything is logged (logs/monitor/DATE.md, the audit log)
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
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import gemma_review as grv  # scripts/ is on the path when this script runs
import gemma_trade as gtr

from scanner.advisor import gemma_memory as gm
from scanner.advisor import gemma_trader as gt
from scanner.config import settings
from scanner.execution import ownership, sell_queue, sessions
from scanner.execution.alpaca import Alpaca
from scanner.execution import satellite as sat
from scanner.execution.ownership import CORE, GEMMA, ORB, PICKS
from scanner.execution.scorecard import read_rows
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import EXCHANGE_TZ
from scanner.features.technicals import average_true_range_pct
from scanner.llm.ollama import LLMError, chat_json
from scanner.ops import market_monitor as mm
from scanner.ops import notify
from scanner.ops import pre_close
from scanner.ops import shock_alert as sa
from scanner.ops import shock_feed
from scanner.sources import composite
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


def queue_sells(items: dict[str, str]) -> None:
    """Hand sells to peak watch (execution/sell_queue.py): it is the one automatic seller,
    every 5 minutes, 24/5 -- so two scripts never sell the same position."""
    path = Path(sell_queue.PATH)
    try:
        queue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        queue = {}
    path.write_text(json.dumps(sell_queue.add(queue, items), indent=1), encoding="utf-8")


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


async def researched_answer(build, facts: dict[str, str], schema: dict, client, broker, lines: list[str],
                            *, ask: str, calls: int) -> tuple[dict, dict[str, str]]:
    """Her thinking step with read-only tools (Gemma 2026-10-24), then her answer with what she looked
    up added to each stock's facts -- so a quote from a lookup is checked like any fact. `build(facts)`
    makes the question. (answer, facts) -- raises LLMError only if she cannot answer at all."""
    notes, made = "", []
    if gt.THINKS:
        try:
            notes, made = await gtr.research_notes(build(facts), client, broker, max_tokens=gt.THINK_TOKENS,
                                                   ask=ask, calls=calls)
        except LLMError as exc:
            log(lines, f"  research failed ({exc}) -- answering from the facts alone")
    for c in made:
        log(lines, f"  lookup: {c['tool']}({', '.join(f'{k}={v}' for k, v in dict(c['args']).items())})")
    facts = mm.with_lookups(facts, made)
    return await chat_json(gt.with_notes(build(facts), notes), schema, client), facts


async def pre_close_pass(client, broker, positions, owner_map, orders, lines: list[str], dry: bool,
                         fired: list, skip: set[str]) -> list[str]:
    """3:15 PM ET under a bellwether / theme-shock alert (ops/pre_close.py): she looks things up and
    says keep or sell for each holding before the close. Sells are queued for peak watch."""
    today = datetime.now(EXCHANGE_TZ).date()
    mine = [p for p in positions if ownership.owner(p["symbol"], owner_map) == GEMMA and p["symbol"] not in skip]
    if not mine:
        return []
    log(lines, f"  PRE-CLOSE CHECK (alert: {', '.join(a.kind for a in fired)})")
    try:
        sectors = {m.symbol: m.sector for m in await composite.load_universe(client)}
    except Exception as exc:                                  # noqa: BLE001
        log(lines, f"  sectors unavailable ({type(exc).__name__}) -- the minimum hold applies to every name")
        sectors = {}
    entered = gtr.gemma_entry_dates(orders)
    symbols = [p["symbol"] for p in mine]
    facts, trend, _ = await gtr.facts_for(symbols, client, sessions.REGULAR)
    rows, allowed = [], {}
    for p in mine:
        s = p["symbol"]
        held = sat.trading_days_between(entered[s], today) if s in entered else sat.MIN_HOLD_DAYS
        allowed[s] = pre_close.may_sell(held_days=held, min_hold=sat.MIN_HOLD_DAYS, sector=sectors.get(s))
        rows.append((s, float(p["unrealized_plpc"]) * 100, (await quote(client, s))[1], held, allowed[s]))
    try:
        answer, _ = await researched_answer(
            lambda f: pre_close.prompt(f, rows, alerts=fired, market=trend, today=today), facts,
            pre_close.schema(symbols), client, broker, lines, ask=gt.PRE_CLOSE_ASK, calls=gt.PRE_CLOSE_CALLS)
    except LLMError as exc:
        log(lines, f"  Gemma unavailable ({exc}) -- pre-close check skipped, her book unchanged")
        return []
    for h in (answer or {}).get("holdings", []):
        log(lines, f"  pre-close {h.get('symbol'):<6} {h.get('action'):<5} {str(h.get('reason', ''))[:160]}")
    to_sell, held_back = pre_close.sells(answer, allowed)
    for s in held_back:
        log(lines, f"  kept {s}: inside the minimum hold and not in the alerted theme")
    sold = []
    for s, why in to_sell:
        reason = f"pre-close check under an alert: {why}"
        if dry:
            log(lines, f"  WOULD SELL Gemma {s}: {reason}")
            continue
        queue_sells({s: reason})
        log(lines, f"  QUEUED Gemma {s} for peak watch (sells within 5 min): {reason}")
        sold.append(s)
    return sold


async def gemma_sell_ifs(client, broker, positions, owner_map, open_orders, lines, dry: bool,
                         alerts: list[str], peak: str = "", session: str = sessions.REGULAR,
                         entered: dict[str, datetime] | None = None) -> list[str]:
    mine = [p for p in positions if ownership.owner(p["symbol"], owner_map) == GEMMA]
    if not mine:
        log(lines, "  Gemma holds nothing")
        return []
    # No sell-if sale on the day of the buy (Gemma, 2026-10-23); her stops still hold.
    entered = entered or {}
    now = datetime.now().astimezone()
    fresh = [p["symbol"] for p in mine if mm.bought_today(entered.get(p["symbol"]), now)]
    if fresh:
        log(lines, f"  bought today, sell-if checks start tomorrow: {', '.join(fresh)}")
    mine = [p for p in mine if p["symbol"] not in fresh]
    if not mine:
        return []
    sell_ifs = gm.sell_ifs(read_rows(settings.audit_log_path))
    symbols = [p["symbol"] for p in mine]
    facts, trend, _ = await gtr.facts_for(symbols, client, session)
    rows = []
    for p in mine:
        _, day = await quote(client, p["symbol"])
        when = entered.get(p["symbol"])
        bought = (f"bought {when.astimezone(mm.ET):%a %m-%d %H:%M} ET at {float(p['avg_entry_price']):,.2f}"
                  if when else f"bought at {float(p['avg_entry_price']):,.2f}")
        rows.append((p["symbol"], sell_ifs.get(p["symbol"], ""), float(p["unrealized_plpc"]) * 100, day,
                     bought))
    try:
        answer, facts = await researched_answer(
            lambda f: mm.sell_if_prompt(f, rows, market=trend, alerts=alerts, peak=peak), facts,
            mm.sell_if_schema(symbols), client, broker, lines, ask=gt.SELL_IF_ASK, calls=gt.SELL_IF_CALLS)
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
        queue_sells({s: reason})
        log(lines, f"  QUEUED Gemma {s} for peak watch (sells within 5 min): {reason}")
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


async def shock_pass(client, broker, positions, owner_map, session: str, lines: list[str],
                     dry: bool) -> list[str]:
    """Crash / shock alerts (ops/shock_alert.py, operator + Gemma 2026-10-23). Returns the texts of
    today's alerts for her (they wake her; they sell nothing). Never fails the run."""
    try:
        today = datetime.now(EXCHANGE_TZ).date()
        mine = [p["symbol"] for p in positions if ownership.owner(p["symbol"], owner_map) == GEMMA]
        sectors = {m.symbol: m.sector for m in await composite.load_universe(client)}
        theme = sa.theme_holdings(mine, sectors)
        r = await shock_feed.readings(client, session)
        late = session != sessions.REGULAR or datetime.now(EXCHANGE_TZ).hour >= 14
        reporting = (await shock_feed.reporting_bellwethers(client, broker, today)
                     if late and len(theme) >= sa.NIGHT_BEFORE_MIN_NAMES else [])
        alerts = sa.classify(spy=r["spy"], qqq=r["qqq"], smh=r["smh"], vix=r["vix"], up_share=r["up_share"],
                             theme_held=theme, reporting=reporting, regular=session == sessions.REGULAR)
        fmt = lambda v: "n/a" if v is None else f"{v:+.1f}%"        # noqa: E731
        log(lines, f"  shock check: SPY {fmt(r['spy'])}, QQQ {fmt(r['qqq'])}, SMH {fmt(r['smh'])}, "
                   f"VIX {r['vix'] if r['vix'] is not None else 'n/a'}, "
                   f"S&P stocks up {'n/a' if r['up_share'] is None else f'{r['up_share'] * 100:.0f}%'}; "
                   f"Gemma's AI-theme holdings {theme or 'none'}; bellwethers reporting {reporting or 'none'}")
        for a in alerts:
            log(lines, f"  ALERT {a.kind}: {a.text}")
        if dry:
            return [a.text for a in alerts]
        days = await broker._get("/v2/calendar", start=(today + timedelta(days=1)).isoformat(),
                                 end=(today + timedelta(days=7)).isoformat())
        nxt = date.fromisoformat(days[0]["date"]) if days else today + timedelta(days=1)
        shock_feed.store(alerts, today, until_next=nxt)
        return [a.text for a in sa.active(shock_feed.read(), today)]
    except Exception as exc:                                  # noqa: BLE001
        log(lines, f"  shock check unavailable ({type(exc).__name__}: {exc})")
        return []


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
        shocks = await shock_pass(client, broker, positions, owner_map, session, lines, dry)
        sold = await gemma_sell_ifs(client, broker, positions, owner_map, open_orders, lines, dry, shocks,
                                    peak, session, entered=entry_times(orders, GEMMA))
        if sold and not dry:
            body = f"Gemma's sell-if met for {', '.join(sold)} ({session}) -- queued to sell"
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
                queue_sells(dict(to_sell))
                alerts.append(f"Queued your picks {', '.join(s for s, _ in to_sell)} to sell: {to_sell[0][1]}")
        # Gemma's own book: her sell-ifs once an hour, AND whenever a NEW alarm touches her book
        # (operator 2026-10-22: "lets have the alert go to Gemma") -- each alarm wakes her once a day.
        hers = [mm.position_alarm(h) for h in books["gemma"]]
        gemma_alerts = ([f"market: {reason}"] if reason else []) + [a for a in hers if a]
        gemma_alerts += await shock_pass(client, broker, positions, owner_map, session, lines, dry)
        told = set(state.get("told_gemma", []))
        new = [a.split(" ")[0] if not a.startswith("market") else "market" for a in gemma_alerts]
        fresh = [k for k in new if k not in told]
        sold: list[str] = []
        if books["gemma"] and (hourly or fresh):
            if fresh and not hourly:
                log(lines, f"  waking Gemma for new alarms: {', '.join(fresh)}")
            peak = await peak_status(client, positions, owner_map, stops, lines, GEMMA) if books["gemma"] and hourly else ""
            sold = await gemma_sell_ifs(client, broker, positions, owner_map, open_orders, lines, dry, gemma_alerts, peak,
                                        entered=entry_times(orders, GEMMA))
            state["told_gemma"] = sorted(told | set(new))
            if sold:
                alerts.append(f"Gemma's sell-if met for {', '.join(sold)} -- queued to sell")
        # 3:15 PM ET, once a day, only under a bellwether / theme-shock alert (Gemma 2026-10-24).
        if books["gemma"] and pre_close.due(datetime.now(EXCHANGE_TZ), state.get("pre_close_day")):
            fired = pre_close.triggers(sa.active(shock_feed.read(), now.date()))
            if fired:
                state["pre_close_day"] = now.date().isoformat()
                late = await pre_close_pass(client, broker, positions, owner_map, orders, lines, dry, fired, set(sold))
                if late:
                    alerts.append(f"Gemma's pre-close check queued {', '.join(late)} to sell")
            else:
                log(lines, "  pre-close check: no bellwether / theme-shock alert -- not needed today")
        if books["picks"] and hourly:
            await peak_status(client, positions, owner_map, stops, lines, PICKS)
        for a in [x for x in books["other"]]:
            who = ownership.owner(a.symbol, owner_map)
            log(lines, f"  note: {a.symbol} is held by the {who} book -- alerts only"
                if who in (CORE, ORB) else f"  note: {a.symbol} is held outside both books (hand trade) -- alerts only")
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
