"""Gemma's book: the junior trader's morning decision (WORKLIST §42).

    python scripts/gemma_trade.py            # ask Gemma, show the decision, send nothing
    python scripts/gemma_trade.py --submit   # ask, then trade it (05:45 AK task)

1  The list: watchlists/core.txt + watchlists/gemma.txt, minus names another book holds or has ordered,
   minus watchlists/picks.txt and momentum's current picks (reserved even
   before they are bought), plus whatever Gemma's book holds now.
2  Facts per name, computed here (Gemma writes no numbers): 1/3/12-month and
   12-1 momentum returns, daily ATR, the S&P trend, earnings within 7 days,
   recent headlines (TradingAgents is a shadow Gemma does not see).
3  Gemma answers holdings (symbols from the list only) + reason + quoted
   evidence + a journal line (advisor/gemma_trader.py validates it).
4  Sell what it dropped, buy what it added -- priced, sized and stopped by
   execution/bookorders.py exactly like the picks list -- on its own sleeve
   (`gemma_allocation_pct`) and slots (`gemma_max_positions`).

Every decision is logged as `gemma_decision` with the full answer, so the
book's results can be traced to the reasons Gemma gave.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta, timezone

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import gemma_pool as gp
from scanner.advisor import gemma_trader as gt
from scanner.advisor import market_context as mc
from scanner.advisor import memory_feed
from scanner.advisor import opinions
from scanner.advisor import wall_street
from scanner.advisor.scan_watch import operator_names
from scanner.config import settings
from scanner.execution import bookorders, ownership, picks, scorecard, sessions
from scanner.execution.alpaca import Alpaca
from scanner.execution.guards import RiskLimits, check_account
from scanner.execution.ownership import GEMMA
from scanner.execution.policy import TradingPolicy
from scanner.execution.safety import kill_switch_active
from scanner.execution.scorecard import read_rows
from scanner.features.bars import fetch_bars, fetch_many
from scanner.momentum import state as mstate
from scanner.ops import competition
from scanner.ops import ghost_scanner
from scanner.features.intraday_context import atr_pct
from scanner.features.technicals import compute, relative_strength
from scanner.llm.ollama import LLMError, chat_json, chat_text
from scanner.sources import calendar as event_calendar
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP500
from scanner.sources import yahoo_rss
from pathlib import Path

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"
POLICY = TradingPolicy()
BRIEF_DIR = Path("data/brief")
SNAPSHOTS = Path("data/competition/snapshots.jsonl")
PAPER_BOOKS = Path("data/competition/paper_books.json")
GHOST_SCANNER = Path("data/competition/ghost_scanner.json")
QUESTIONS = Path("data/gemma/questions")


def daily_brief(day: date) -> dict | None:
    """Today's brief from scripts/week_ahead.py, or None. A stale or broken file
    is ignored: yesterday's news must never pass for today's."""
    try:
        brief = json.loads((BRIEF_DIR / f"{day.isoformat()}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return brief if brief.get("date") == day.isoformat() and brief.get("market_text") else None
#: Her briefing, trimmed 2026-10-19 (operator: "trimming down into tokens"):
#: headlines were 41% of it. Three per stock, each cut to HEADLINE_CHARS.
HEADLINES_PER_STOCK = 3
HEADLINE_CHARS = 120
CORE = Path("watchlists/core.txt")
GEMMA_LIST = Path("watchlists/gemma.txt")


def _ret(closes, back: int) -> str:
    return f"{(closes[-1] / closes[-back - 1] - 1) * 100:+.1f}%" if len(closes) > back else "n/a"


async def facts_for(symbols, client) -> tuple[dict[str, str], str, bool]:
    """(facts per stock, the market line, whether the S&P 500 is above its 210-day average)."""
    daily, _ = await fetch_many(tuple(symbols), client, range_="2y")
    spx = await fetch_bars("^GSPC", client, range_="2y")
    sma = sum(spx.closes[-210:]) / 210
    trend = (f"S&P 500 {spx.closes[-1]:,.0f} is {'ABOVE' if spx.closes[-1] > sma else 'BELOW'} "
             f"its 210-day average {sma:,.0f}")
    # Market context Gemma asked for (WORKLIST §48): context only, labelled.
    bench = compute(spx)
    members = await composite.load_universe(client, COMPOSITE_1500)
    sector = {m.symbol: m.sector for m in members}
    try:
        vix = await fetch_bars("^VIX", client, range_="1y")
        tnx = await fetch_bars("^TNX", client, range_="3mo")
        sp500 = [m.symbol for m in await composite.load_universe(client, (SP500,))]
        wide, _ = await fetch_many(tuple(sp500), client, range_="6mo")
        width = mc.breadth(b.closes for b in wide.values())
        upcoming = {}
        for k in range(0, 8):
            day = date.today() + timedelta(days=k)
            if day.weekday() < 5 and len(upcoming) < 5:
                upcoming[f"{day:%a %m/%d}"] = await event_calendar.market_moving_events(client, on=day)
        trend += ". " + "; ".join((
            mc.vix_text(vix.closes), mc.yield_text(tnx.closes),
            f"{width:.0f}% of S&P 500 stocks are above their 50-day average" if width is not None
            else "breadth unavailable",
            mc.events_text(upcoming)))
        trend += ". (Context only: none of these was a tested edge on this desk.)"
    except Exception as exc:                             # noqa: BLE001
        trend += f". (market context unavailable: {type(exc).__name__})"
    try:
        earnings, _failed = await event_calendar.upcoming_earnings(frozenset(symbols), client)
    except Exception:                                    # noqa: BLE001
        earnings = {}
    rows = read_rows(settings.audit_log_path)
    out: dict[str, str] = {}
    for s in symbols:
        b = daily.get(s)
        if b is None or len(b.closes) < 30:
            continue
        f = compute(b)
        mom = f"{f.momentum_12_1:+.1f}%" if f.momentum_12_1 is not None else "n/a"
        atr = atr_pct(b)
        atr_text = f"{atr:.1f}%" if atr else "n/a"
        rs = relative_strength(f, bench)
        lines = [f"{s} ({sector.get(s, 'sector unknown')}) | 1m {_ret(b.closes, 21)} | "
                 f"3m {_ret(b.closes, 63)} | 12m {_ret(b.closes, 252)} | 12-1 momentum {mom}"
                 + (f" ({rs:+.0f} pts vs the S&P 500)" if rs is not None else "")
                 + f" | daily ATR {atr_text}"]
        report = earnings.get(s)
        if report:      # no line = none within 7 days (her rules say so)
            lines.append(f"earnings: {report.label}")
        # TradingAgents runs as a SHADOW (2026-10-07): Gemma asked not to see it
        # ("a black box that might lead me to mimic the system's logic") so its
        # record is its own. TradingAgents is still rated, graded, and shown to
        # the operator; policy.gemma_sees_tradingagents turns this back on.
        ta = (opinions.latest_calls(rows, s).get("tradingagents")
              if POLICY.gemma_sees_tradingagents else None)
        if ta:
            # The verdict AND a short reason from its report (2026-10-07), so
            # Gemma weighs why, not just what. Labelled: its reasons lean on
            # chart indicators, which tested as no edge here.
            why = ""
            try:
                raw = json.loads(Path(ta["report"]).read_text(encoding="utf-8"))
                why = opinions.report_summary(raw.get("final_trade_decision", ""))
            except (OSError, ValueError, KeyError):
                pass
            lines.append(f"TradingAgents (unproven; leans on chart indicators) said "
                         f"{ta['call']} on {ta['at']}" + (f": {why}" if why else ""))
        try:
            items = await yahoo_rss.fetch(s, client)
            # Opinion and listicles left out (2026-10-18): a quoted headline is
            # her evidence for a buy, so "What $5,000 Could Be Worth by 2028"
            # must not be quotable. Same filter as the brief's Wall Street lines.
            news = [i for i in items if not wall_street.is_opinion(i.title)]
            for item in news[:HEADLINES_PER_STOCK]:
                lines.append(f"headline: {item.title[:HEADLINE_CHARS]}")
        except Exception:                                # noqa: BLE001
            lines.append("headlines: unavailable")
        out[s] = "\n".join(lines)
    return out, trend, spx.closes[-1] > sma


def save_question(text: str, schema: dict, facts: dict, held, keep, *,
                  submitted: bool, session: str) -> None:
    """The exact question Gemma was asked, for the shadow bench
    (advisor/gemma_shadow.py). Never allowed to break a trading run."""
    try:
        now = datetime.now().astimezone()
        QUESTIONS.mkdir(parents=True, exist_ok=True)
        path = QUESTIONS / f"{now:%Y-%m-%dT%H%M}-{session}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "id": path.stem, "at": now.isoformat(), "day": now.date().isoformat(),
            "session": session, "submitted": submitted, "text": text, "schema": schema,
            "facts": facts, "held": sorted(held), "live_keep": list(keep),
            "max_positions": POLICY.gemma_max_positions}), encoding="utf-8")
        tmp.replace(path)
    except (OSError, TypeError, ValueError) as exc:
        print(f"  {YELLOW}question not saved for the shadow bench: {exc}{RESET}")


def money_at_work(positions, owner_map, done_dollars: float, sleeve: float) -> str:
    """How much of Gemma's book is working, and its P/L on the WHOLE book."""
    held = [p for p in positions if ownership.owner(p["symbol"], owner_map) == GEMMA]
    working = sum(float(p.get("market_value") or 0) for p in held)
    pl = done_dollars + sum(float(p.get("unrealized_pl") or 0) for p in held)
    empty = max(0, POLICY.gemma_max_positions - len(held))
    if sleeve <= 0:
        return ""
    return (f"Your money at work: ${working:,.0f} of your ${sleeve:,.0f} book invested "
            f"({working / sleeve * 100:.0f}%), {empty} of {POLICY.gemma_max_positions} slots "
            f"empty; P/L on your whole book since your first order {'+' if pl >= 0 else '-'}${abs(pl):,.0f} "
            f"({pl / sleeve * 100:+.2f}% of the book)")


async def ghosts_text(client, month: str) -> str:
    """This month's two ghosts, priced at the latest closes."""
    try:
        raw = json.loads(PAPER_BOOKS.read_text(encoding="utf-8")) if PAPER_BOOKS.exists() else {}
        mine = raw.get(f"{month}:ghost_gemma")
        book = competition.PaperBook.model_validate(mine) if mine else None
        trades = (ghost_scanner.load(GHOST_SCANNER.read_text(encoding="utf-8"))[0]
                  if GHOST_SCANNER.exists() else ())
    except (OSError, ValueError):
        return ""
    open_names = {t.symbol for t in trades if t.exit_price is None}
    wanted = tuple(({n.symbol for n in book.names} if book else set()) | open_names)
    prices, _ = await fetch_many(wanted, client, range_="1mo") if wanted else ({}, ())
    last = {s: b.closes[-1] for s, b in prices.items() if len(b.closes)}
    parts = []
    if book is not None:
        parts.append(f"ghost-Gemma (5 random from your list, same stop) "
                     f"{competition.paper_return(book, last):+.2f}%")
    g = ghost_scanner.summary(trades, month, last)
    if g.closed or g.open:
        avg = f", average {g.mean_pct:+.2f}% per trade" if g.mean_pct is not None else ""
        parts.append(f"ghost-scanner (the sweep scanner's rules on paper) {g.closed} closed "
                     f"trades, {g.won} won, {g.total_r:+.2f}R, {g.open} open{avg}")
    return "Your ghosts this month: " + "; ".join(parts) if parts else ""


def _first_order_day(rows) -> str | None:
    """The day Gemma's book placed its first order: the start of its record."""
    days = [str(r.get("at", ""))[:10] for r in rows
            if r.get("event") == "order_submitted"
            and (r.get("intent") or {}).get("strategy") == GEMMA]
    return min(days) if days else None


async def scoreboard(client, positions, owner_map, sleeve: float) -> str:
    """Gemma's book since its first order, against the S&P 500 and the momentum
    paper book over the same days. Empty before its first order."""
    rows = read_rows(settings.audit_log_path)
    start = _first_order_day(rows)
    if start is None:
        return ""
    by_book = scorecard.by_strategy(rows)

    def on_invested(book: str) -> tuple[float, int]:
        """(% on money invested, closed trades) for one book: realized plus
        unrealized dollars over the cost of what it holds. The SAME measure for
        Gemma and the operator, so the competition is scored like for like."""
        done = by_book.get(book)
        held = [p for p in positions if ownership.owner(p["symbol"], owner_map) == book]
        cost = sum(float(p.get("cost_basis") or 0) for p in held)
        pl = (done.total_dollars if done else 0.0) + sum(
            float(p.get("unrealized_pl") or 0) for p in held)
        return (pl / cost * 100 if cost else 0.0), (done.trades if done else 0)

    mine, closed = on_invested(GEMMA)
    done = by_book.get(GEMMA)
    work = money_at_work(positions, owner_map, done.total_dollars if done else 0.0, sleeve)
    spx = await fetch_bars("^GSPC", client, range_="1y")
    since = [c for d, c in zip(spx.dates, spx.closes) if d.isoformat() >= start]
    spx_ret = (spx.closes[-1] / since[0] - 1) * 100 if since else 0.0
    # The head-to-head comes from the competition's own snapshots, scored as
    # the monthly verdict scores it. on_invested() divides by what a book holds
    # NOW, so an empty picks book read +0.00% here (fixed 2026-10-18).
    try:
        snaps = competition.load_snapshots(SNAPSHOTS.read_text(encoding="utf-8"))
        mtd = competition.month_to_date(snaps, competition.month_of(date.today()))
    except (OSError, ValueError):
        mtd = {}
    text = (f"since your first order on {start}: your book {mine:+.2f}% on money invested "
            f"(closed trades {closed}); S&P 500 {spx_ret:+.2f}%")
    # The benchmark is the momentum RULE's dry-run record (momentum stopped
    # trading 2026-10-07): its holdings priced at today's closes, plus cash,
    # against the capital it started with -- the same tranche book the
    # backtest measured, with no fills to go wrong.
    book = mstate.load(Path("data/momentum/dry_run_book.json"))
    if book is not None:
        held = {s: n for t in book.tranches for s, n in t.holdings.items()}
        if held:
            prices, _ = await fetch_many(tuple(held), client, range_="1mo")
            value = sum(t.cash for t in book.tranches) + sum(
                n * prices[s].closes[-1] for s, n in held.items() if s in prices)
            first = json.loads(Path("data/momentum/dry_run.jsonl").read_text(
                encoding="utf-8").splitlines()[0])
            start_value = sum(t.cash for t in book.tranches) + sum(
                n * float(first["prices"].get(s, 0)) for s, n in held.items())
            text += f", momentum rule {(value / start_value - 1) * 100:+.2f}%"
    if mtd:
        def score(book: str) -> str:
            return f"{mtd[book]:+.2f}%" if book in mtd else "not scored yet"
        text += (f". This month so far, scored as the monthly verdict scores it (change in "
                 f"P/L over average money invested): your book {score(GEMMA)}, the "
                 f"operator's picks book {score(ownership.PICKS)}")
    ghosts = await ghosts_text(client, competition.month_of(date.today()))
    if ghosts:
        text += ". " + ghosts
    if work:
        text += ". " + work
    recent = []
    for r in rows:
        if r.get("event") == "gemma_decision":
            try:
                body = json.loads(r.get("reason", "{}"))
            except ValueError:
                continue
            if body.get("submitted"):
                recent.append({"day": str(r["at"])[:10], "buys": body.get("buys", []),
                               "sells": body.get("sells", [])})
    text += ". " + mc.decisions_text(recent[-5:])
    sel_file = Path("data/competition/selection.json")
    if sel_file.exists():
        try:
            g = json.loads(sel_file.read_text(encoding="utf-8"))["gemma"]["5"]
            text += ("; your selection record (chosen minus passed-over names, 5 sessions on): "
                     + (f"{g['mean']:+.2f} pts over {g['days']} graded days, positive on "
                        f"{g['positive_days']}" if g["days"] else "no day graded yet"))
        except (ValueError, KeyError):
            pass
    return text


async def think_then_answer(text: str, answer_schema: dict, client,
                            *, max_tokens: int = gt.THINK_TOKENS) -> tuple[dict, str]:
    """She thinks in plain words first (capped), then answers with her notes in
    front of her. A failed thinking pass never blocks the answer. (answer, notes)"""
    notes = ""
    if gt.THINKS:
        started = time.monotonic()
        try:
            notes = await chat_text(gt.think_prompt(text), client, max_tokens=max_tokens)
            print(f"  {DIM}thought for {time.monotonic() - started:.0f}s "
                  f"({len(notes.split())} words){RESET}")
        except LLMError as exc:
            print(f"  {YELLOW}thinking failed ({exc}) -- answering without notes{RESET}")
    return await chat_json(gt.with_notes(text, notes), answer_schema, client), notes


async def pool_shortlist(client, taken: set[str], mine: Iterable[str],
                         mom_picks: Sequence[str]) -> tuple[list[str], int]:
    """Round 1 (advisor/gemma_pool.py): the S&P 500 through the scanner's floor,
    shortlisted by Gemma. (shortlist, pool size); an empty shortlist means fall back."""
    mine = set(mine)
    members = await composite.load_universe(client, (SP500,))
    sectors = {m.symbol: m.sector for m in members}
    names = tuple(s for s in sectors if s not in taken and s not in mine)
    daily, _ = await fetch_many(names, client, range_="2y")
    bars = {s: b for s, b in daily.items()
            if b is not None and len(b.closes) >= 30 and gp.tradeable(
                b.closes, b.highs, b.lows, b.volumes, min_price=POLICY.min_price,
                min_range_pct=POLICY.min_range_pct, min_dollar_volume=POLICY.min_dollar_volume)}
    momentum = {s: compute(b).momentum_12_1 for s, b in bars.items()}
    rows = gp.rows_for(bars, sectors, momentum=momentum,
                       atr={s: atr_pct(b) for s, b in bars.items()},
                       momentum_rank={s: i for i, s in enumerate(mom_picks, 1)})
    spx = await fetch_bars("^GSPC", client, range_="2y")
    sma = sum(spx.closes[-210:]) / 210
    market = (f"S&P 500 {spx.closes[-1]:,.0f} is {'ABOVE' if spx.closes[-1] > sma else 'BELOW'} "
              f"its 210-day average {sma:,.0f}")
    text = gp.prompt(rows, held=mine, market=market)
    try:
        answer, _ = await think_then_answer(text, gp.schema([r.symbol for r in rows]),
                                            client, max_tokens=gt.THINK_TOKENS // 2)
    except LLMError as exc:
        print(f"  {YELLOW}pool shortlist failed ({exc}) -- using the hand lists{RESET}")
        return [], len(rows)
    picked = gp.validate(answer, [r.symbol for r in rows], held=mine)
    print(f"  {BOLD}pool{RESET}: {len(rows)} tradeable S&P 500 stocks; she shortlisted "
          f"{list(picked)}")
    if isinstance(answer, dict) and answer.get("looking_for"):
        print(f"  {DIM}looking for: {str(answer['looking_for'])[:300]}{RESET}")
    return list(picked), len(rows)


MENU = gt.Menu(stops=tuple(k for k, _ in POLICY.gemma_stop_atr_menu),
               targets=tuple(k for k, _ in POLICY.gemma_target_r_menu),
               sizes=tuple(k for k, _ in POLICY.gemma_size_risk_menu))
MENU_TEXT = "\n".join((
    "- stop: " + ", ".join(f"{k} = {v:g} daily ATRs under the entry"
                          for k, v in POLICY.gemma_stop_atr_menu),
    "- target: " + ", ".join("none = no target, ride it" if v == 0 else
                            f"{k} = take profit at {v:g}x the risk above the entry"
                            for k, v in POLICY.gemma_target_r_menu),
    "- size: " + ", ".join(f"{k} = risk {v:g}% of your book if stopped"
                          for k, v in POLICY.gemma_size_risk_menu)
    + " (one stock is never more than a fifth of your book)",
))


async def main(submit: bool) -> int:
    async with httpx.AsyncClient(timeout=120) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        raw = await broker._get("/v2/account")
        account = await broker.account()
        positions = await broker._get("/v2/positions")
        open_orders = await broker._get("/v2/orders", status="open", nested="true")
        owner_map = ownership.owners(await broker._get(
            "/v2/orders", status="all", limit=500, nested="true"))
        # Extended hours (WORKLIST §59): outside regular hours the orders go as
        # limit orders and protect.py arms the stops at the open.
        session = await sessions.current(broker)
        market_open = session == sessions.REGULAR
        tradable = session == sessions.REGULAR or (
            session in sessions.EXTENDED and POLICY.extended_hours_trading)

        if submit and tradable:
            for order in picks.stale_entries(open_orders, datetime.now(timezone.utc),
                                             minutes=POLICY.picks_stale_entry_minutes,
                                             strategy=GEMMA):
                try:
                    await broker.cancel_order(str(order["id"]))
                    bookorders.log("gemma_entry_cancelled", order["symbol"],
                                   "unfilled since the last decision", POLICY)
                except Exception as exc:                  # noqa: BLE001
                    print(f"  {RED}cancel {order['symbol']}: {exc}{RESET}")
            open_orders = await broker._get("/v2/orders", status="open", nested="true")
        mine = {p["symbol"]: p for p in positions
                if ownership.owner(p["symbol"], owner_map) == GEMMA}
        taken = {p["symbol"] for p in positions if p["symbol"] not in mine}
        taken |= {o["symbol"] for o in open_orders
                  if ownership.strategy_of(o.get("client_order_id")) != GEMMA}
        # Reserved for other books even before they buy: the operator's picks
        # list trades at 05:33, momentum at 11:40 on rebalance days. Momentum is
        # the one tested rule; letting Gemma take its names first would quietly
        # change what is being tested.
        taken |= set(picks.read_list()[0])
        # Momentum no longer trades (2026-10-07): its monthly picks are OPTIONS
        # for Gemma, labelled in the facts, instead of names reserved for it.
        if POLICY.momentum_trades:
            taken |= set(opinions.momentum_picks()[1])
        mom_day, mom_picks, _ = opinions.momentum_picks()
        extra = () if POLICY.momentum_trades else mom_picks
        universe = list(dict.fromkeys((*operator_names(CORE), *operator_names(GEMMA_LIST), *extra)))
        allowed = [s for s in universe if s not in taken]
        allowed += [s for s in mine if s not in allowed]
        # The stock pool (operator 2026-10-19): she looks at the whole S&P 500
        # first and shortlists; round 2 is her shortlist plus her holdings. The
        # hand lists above are the fallback if round 1 fails.
        pool_size = 0
        if gp.ENABLED:
            try:
                short, pool_size = await pool_shortlist(client, taken, mine, mom_picks)
            except Exception as exc:                      # noqa: BLE001
                short = []
                print(f"  {YELLOW}pool unavailable ({type(exc).__name__}: {exc}) -- "
                      f"using the hand lists{RESET}")
            if short:
                allowed = [*short, *(s for s in mine if s not in short)]
        print(f"{BOLD}Gemma's book{RESET}  sleeve {POLICY.gemma_allocation_pct:.0f}% "
              f"(${account.equity * POLICY.gemma_allocation_pct / 100:,.0f}), "
              f"up to {POLICY.gemma_max_positions} names")
        print(f"  holds {sorted(mine) or 'nothing'}; may choose from {allowed}")
        if not allowed:
            print("  nothing on the list is free -- no decision today")
            return 0

        facts, trend, uptrend = await facts_for(allowed, client)
        least = gt.MIN_IN_UPTREND if uptrend else 0
        if not POLICY.momentum_trades:
            for rank, s in enumerate(mom_picks, 1):
                if s in facts:
                    facts[s] += (f"\nmomentum rule: #{rank} of its 10 picks for this month "
                                 f"(formed {mom_day}) -- the rule you are measured against")
        # The 03:00 daily brief (WORKLIST §52): coming events, the President, the
        # news, and which of these stocks each item touches. Facts, not its read.
        brief = daily_brief(date.today()) if POLICY.gemma_sees_brief else None
        if brief:
            trend += ". " + brief["market_text"]
            street = wall_street.for_gemma(brief.get("wall_street") or [], facts)
            if street and "Wall Street since" not in brief["market_text"]:
                trend += (". Wall Street since the last session, your stocks and the Fed: "
                          + " | ".join(street))
            for s, lines in brief.get("stock_lines", {}).items():
                if s in facts and lines:
                    facts[s] += "\n" + "\n".join(lines)
        elif POLICY.gemma_sees_brief:
            print(f"  {YELLOW}no daily brief for today -- deciding without it{RESET}")
        held_status ={s: f"{float(p.get('unrealized_plpc') or 0) * 100:+.1f}% since entry"
                       for s, p in mine.items()}
        sleeve_dollars = account.equity * POLICY.gemma_allocation_pct / 100
        try:
            score = await scoreboard(client, positions, owner_map, sleeve_dollars)
        except Exception as exc:                          # noqa: BLE001
            score = ""
            print(f"  {YELLOW}scoreboard unavailable: {exc}{RESET}")
        text = gt.prompt(facts, held=held_status, max_positions=POLICY.gemma_max_positions,
                         scoreboard=score, sleeve=f"${sleeve_dollars:,.0f}",
                         menu=MENU, menu_text=MENU_TEXT, session=session, min_positions=least,
                         graded=True)
        # Her short memory (operator 2026-10-19): her own record with what
        # happened since, built from outcomes and prices (advisor/gemma_memory.py).
        memory = await memory_feed.memory_block(client, held=list(mine))
        text = text.replace("The list:", (f"{memory}\n\n" if memory else "")
                            + f"Market: {trend}.\n\nThe list:", 1)
        answer_schema = gt.schema(list(facts), POLICY.gemma_max_positions, menu=MENU,
                                  min_positions=least, graded=True)
        notes = ""
        try:
            answer, notes = await think_then_answer(text, answer_schema, client)
        except LLMError as exc:
            answer = None
            # Upper case on purpose: the desk journal's error check looks for it.
            print(f"  {RED}FAILED: Gemma unavailable ({exc}) -- book left unchanged{RESET}")
        decision = gt.validate(answer, facts, held=set(mine),
                               max_positions=POLICY.gemma_max_positions, menu=MENU)
        buys, sells = gt.diff(decision.keep, mine)
        bookorders.log("gemma_decision", "", json.dumps({
            "keep": decision.keep, "buys": buys, "sells": sells,
            "reasons": dict(decision.reasons), "rejected": dict(decision.rejected),
            "journal": decision.journal, "unchanged": decision.unchanged,
            # Menu choices, for grading which choices paid (WORKLIST §50).
            "plans": {pl.symbol: {"stop": pl.stop, "target": pl.target, "size": pl.size}
                      for pl in decision.plans if pl.symbol in buys},
            # The whole menu, so the names Gemma PASSED OVER can be graded
            # against the ones it chose (ops/selection.py, WORKLIST §47).
            "menu": sorted(facts),
            "submitted": submit and tradable, "session": session,
            "pool_size": pool_size, "thinking": notes,
            "memory": memory,
            # WORKLIST §65: her 5-day probabilities (graded by advisor/forecast_grade.py)
            # and the observable exit she names for each holding.
            "forecasts": dict(decision.forecasts), "sell_if": dict(decision.sell_if)}), POLICY)
        save_question(text, answer_schema, facts, mine, decision.keep, submitted=submit and tradable,
                      session=session)

        print(f"\n  {BOLD}Gemma holds{RESET} {list(decision.keep) or 'nothing'}")
        plans = {pl.symbol: pl for pl in decision.plans}
        for s, why in decision.reasons:
            pl = plans[s]
            menu = f" [{pl.stop} stop, target {pl.target}, {pl.size}]" if s in buys else ""
            print(f"    {s:<6}{menu} {why}")
        for s, why in decision.rejected:
            print(f"    {DIM}{s:<6} not taken -- {why}{RESET}")
        if decision.journal:
            print(f"  {DIM}journal: {decision.journal}{RESET}")
        for s, cond in decision.sell_if:
            print(f"  {DIM}{s:<6} sell if: {cond}{RESET}")
        if decision.forecasts:
            top = sorted(decision.forecasts, key=lambda f: -f[1])
            print(f"  {DIM}forecasts ({len(top)}): "
                  + ", ".join(f"{s} {p:.2f}" for s, p in top) + RESET)
        print(f"  plan: buy {list(buys) or 'nothing'}; sell {list(sells) or 'nothing'}")

        if not tradable:
            print(f"  {DIM}market closed -- nothing sent{RESET}")
            return 0
        if not submit:
            print(f"  {YELLOW}preview -- re-run with --submit{RESET}")
            return 0

        for s in sells:
            ok = await bookorders.sell(broker, s, open_orders, strategy=GEMMA,
                                       reason="Gemma dropped it", policy=POLICY,
                                       session=session, client=client)
            print(f"  {GREEN if ok else RED}{'sold' if ok else 'SELL FAILED'}{RESET} {s}")
        if kill_switch_active(settings.kill_switch_path):
            print(f"  {RED}kill switch active -- no buys{RESET}")
            return 0
        limits = RiskLimits(max_positions=POLICY.gemma_max_positions,
                            max_daily_loss_pct=POLICY.max_daily_loss_pct,
                            min_equity=POLICY.min_equity)
        sleeve = account.equity * POLICY.gemma_allocation_pct / 100
        deployed = sum(float(mine[s].get("market_value") or 0) for s in mine if s not in sells)
        room = max(0.0, min(account.buying_power, sleeve - deployed))
        cap = 100.0 / POLICY.gemma_max_positions
        count = len([s for s in mine if s not in sells])
        reasons = dict(decision.reasons)
        for s in buys:
            gate = check_account(account.equity, float(raw.get("last_equity") or account.equity),
                                 count, limits, market_open=True,
                                 account_status=account.status, account_blocked=account.blocked)
            if not gate:
                print(f"  {YELLOW}{s:<6} not bought -- {gate.reason}{RESET}")
                bookorders.log("gemma_refused", s, gate.reason, POLICY)
                continue
            pl = plans[s]
            result = await bookorders.buy_with_stop(
                broker, client, s, strategy=GEMMA, sleeve=sleeve, room=room, cap_pct=cap,
                policy=POLICY,
                reason=f"Gemma [{pl.stop} stop, target {pl.target}, {pl.size}]: "
                       f"{reasons.get(s, '')}",
                risk_pct=POLICY.gemma_menu_value("size", pl.size),
                atr_multiple=POLICY.gemma_menu_value("stop", pl.stop),
                reward_risk=POLICY.gemma_menu_value("target", pl.target), session=session)
            if not result.ok:
                print(f"  {YELLOW}{s:<6} not bought -- {result.message}{RESET}")
                continue
            count += 1
            room -= result.sizing.position_dollars
            print(f"  {GREEN}bought{RESET} {s:<6} {result.message}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--submit", action="store_true", help="actually trade the decision")
    raise SystemExit(asyncio.run(main(p.parse_args().submit)))
