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
from datetime import date, datetime, timedelta, timezone

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import gemma_trader as gt
from scanner.advisor import market_context as mc
from scanner.advisor import opinions
from scanner.advisor.scan_watch import operator_names
from scanner.config import settings
from scanner.execution import bookorders, ownership, picks, scorecard, sessions
from scanner.execution.alpaca import Alpaca
from scanner.execution.guards import RiskLimits, check_account, sector_concentration
from scanner.execution.ownership import GEMMA
from scanner.execution.policy import TradingPolicy
from scanner.execution.safety import kill_switch_active
from scanner.execution.scorecard import read_rows
from scanner.execution.session import crosses_session, is_stale_session
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import EXCHANGE_TZ, drop_incomplete, fetch_intraday, max_range, session_slot
from scanner.features.intraday_context import atr_pct
from scanner.momentum import state as mstate
from scanner.execution.math_signals import build_signals
from scanner.features.structure import DEFAULT_K
from scanner.features.technicals import compute, relative_strength
from scanner.features.market_snapshot import fetch_snapshot, summary_text
from scanner.llm.ollama import LLMError, chat_json
from scanner.screens.smc import is_live_setup, scan as smc_scan
from scanner.sources import calendar as event_calendar
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP500
from scanner.sources import yahoo_rss
from pathlib import Path

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"
POLICY = TradingPolicy()
BRIEF_DIR = Path("data/brief")


def daily_brief(day: date) -> dict | None:
    """Today's brief from scripts/week_ahead.py, or None. A stale or broken file
    is ignored: yesterday's news must never pass for today's."""
    try:
        brief = json.loads((BRIEF_DIR / f"{day.isoformat()}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return brief if brief.get("date") == day.isoformat() and brief.get("market_text") else None
CORE = Path("watchlists/core.txt")
GEMMA_LIST = Path("watchlists/gemma.txt")
PORTFOLIO_REVIEW = BRIEF_DIR / "portfolio-review.json"


def _portfolio_review_text() -> str:
    """Gemma's read-only portfolio review, or empty string if unavailable."""
    try:
        data = json.loads(PORTFOLIO_REVIEW.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    text = str(data.get("summary_text", "")).strip()
    snap = str(data.get("market_snapshot", {}).get("summary", "")).strip()
    parts = [p for p in [text, snap] if p]
    return " ".join(parts)


def _ret(closes, back: int) -> str:
    return f"{(closes[-1] / closes[-back - 1] - 1) * 100:+.1f}%" if len(closes) > back else "n/a"


async def facts_for(symbols, client) -> tuple[dict[str, str], str]:
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

    # Live market snapshot: futures, bond yields, VIX, commodities, dollar.
    # Gemma sees today's tape when she decides what to buy — context only.
    try:
        snap = await fetch_snapshot(client)
        snap_line = summary_text(snap)
        if snap_line:
            trend += f". Live tape: {snap_line}"
    except Exception:                                    # noqa: BLE001
        pass                                             # snapshot is context, never a blocker
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
        # Math signals: percentage-based context for Gemma's decision
        try:
            current_close = float(b.closes[-1])
            recent_closes = list(b.closes[-20:])
            recent_high = max(recent_closes)
            recent_low = min(recent_closes)
            sma_20 = f.sma20 if f.sma20 else current_close
            # Daily returns for correlation math
            stock_returns = [(b.closes[i] - b.closes[i - 1]) / b.closes[i - 1]
                             for i in range(1, len(b.closes))]
            avg_vol_20d = sum(b.volumes[-20:]) / 20 if len(b.volumes) >= 20 else 0.0
            math_signals = build_signals(
                current_price=current_close,
                entry_price=current_close,  # no entry price for new buys; position_pct = 0
                sma_20=sma_20,
                recent_high=recent_high,
                recent_low=recent_low,
                current_atr_pct=atr if atr else 0.0,
                atr_history=[],  # no stored ATR history yet
                win_probability=0.5,
                avg_win_pct=0.0,
                loss_probability=0.5,
                avg_loss_pct=0.0,
                stock_returns=stock_returns,
                portfolio_returns=[],  # no portfolio return history yet
                current_volume=float(b.volumes[-1]) if b.volumes else 0.0,
                avg_volume_20d=avg_vol_20d,
            )
            for sig in math_signals:
                lines.append(f"math: {sig.name} = {sig.value_pct:+.1f}% "
                             f"(conf {sig.confidence:.0%}) - {sig.explanation}")
        except Exception:  # noqa: BLE001
            pass  # math signals are context, never a blocker
        report = earnings.get(s)
        lines.append(f"earnings: {report.label}" if report else "earnings: none in the next 7 days")
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
            for item in items[:5]:
                lines.append(f"headline: {item.title}")
        except Exception:                                # noqa: BLE001
            lines.append("headlines: unavailable")
        out[s] = "\n".join(lines)
    return out, trend


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
    theirs, _ = on_invested(ownership.PICKS)
    spx = await fetch_bars("^GSPC", client, range_="1y")
    since = [c for d, c in zip(spx.dates, spx.closes) if d.isoformat() >= start]
    spx_ret = (spx.closes[-1] / since[0] - 1) * 100 if since else 0.0
    text = (f"since your first order on {start}: your book {mine:+.2f}% on money invested "
            f"(closed trades {closed}); the operator's competing picks book {theirs:+.2f}%; "
            f"S&P 500 {spx_ret:+.2f}%")
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


async def scan_for_gemma(
    client: httpx.AsyncClient,
    policy: TradingPolicy,
    universe_symbols: tuple[str, ...],
) -> tuple[str, ...]:
    """Scan the S&P 500 for SMC setups that Gemma can choose from.

    Returns symbols that have valid setups and are not already excluded.
    """
    range_ = max_range(policy.interval_minutes)
    session_today = datetime.now(tz=EXCHANGE_TZ).date()

    loaded = await asyncio.gather(
        *(fetch_intraday(s, client, minutes=policy.interval_minutes, range_=range_)
          for s in universe_symbols),
        return_exceptions=True,
    )

    candidates: list[str] = []
    for symbol, bars in zip(universe_symbols, loaded):
        if isinstance(bars, Exception):
            continue
        bars = drop_incomplete(bars, minutes=policy.interval_minutes)
        if not bars.closes:
            continue
        # Simple liquidity check: price >= $5 and dollar volume >= $20M
        closes = bars.closes[-20:]
        volumes = bars.volumes[-20:]
        if not closes or not volumes:
            continue
        last_price = closes[-1]
        dollar_vol = sum(v * c for v, c in zip(volumes, closes)) / len(closes)
        if last_price < 5.0 or dollar_vol < 20_000_000:
            continue
        setups = smc_scan(
            bars,
            k=policy.swing_k,
            min_reward_risk=policy.min_reward_risk,
            min_risk_pct=policy.min_risk_pct,
            require_value_sweep=policy.require_value_sweep,
        )
        for setup in setups:
            age = len(bars) - 1 - setup.trigger_index
            if age >= policy.fresh_bars:
                continue
            if not is_live_setup(bars, setup):
                continue
            if policy.block_overnight_entry and (
                crosses_session(bars, setup.trigger_index)
                or is_stale_session(bars, setup.trigger_index, session=session_today)
            ):
                continue
            candidates.append(symbol)
            break  # Only need one setup per symbol

    return tuple(candidates)


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
        # Sector map for concentration guard across the account.
        universe = await composite.load_universe(client)
        sector_of = {m.symbol: m.sector for m in universe}
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

        # SCAN: Gemma scans the S&P 500 for setups she can choose from
        scan_universe = tuple(m.symbol for m in await composite.load_universe(client, (SP500,)))
        scan_candidates = await scan_for_gemma(client, POLICY, scan_universe)
        if scan_candidates:
            print(f"{BOLD}SCAN{RESET} {len(scan_candidates)} setup(s) found: {', '.join(scan_candidates)}")
        else:
            print(f"{DIM}No scan setups found{RESET}")
        allowed = [s for s in universe if s not in taken]
        # Add scan candidates to the allowed list (they may overlap with watchlist)
        allowed = list(dict.fromkeys([*allowed, *scan_candidates]))
        allowed += [s for s in mine if s not in allowed]
        print(f"{BOLD}Gemma's book{RESET}  sleeve {POLICY.gemma_allocation_pct:.0f}% "
              f"(${account.equity * POLICY.gemma_allocation_pct / 100:,.0f}), "
              f"up to {POLICY.gemma_max_positions} names")
        print(f"  holds {sorted(mine) or 'nothing'}; may choose from {allowed}")
        if not allowed:
            print("  nothing on the list is free -- no decision today")
            return 0

        facts, trend = await facts_for(allowed, client)
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
            for s, lines in brief.get("stock_lines", {}).items():
                if s in facts and lines:
                    facts[s] += "\n" + "\n".join(lines)
        elif POLICY.gemma_sees_brief:
            print(f"  {YELLOW}no daily brief for today -- deciding without it{RESET}")
        # Gemma's portfolio review: read-only snapshot of the whole account
        # (open positions, by-book stats, refusals, sector exposure,
        # TradingAgents on holdings). Advisory only -- nothing in it blocks,
        # sizes, or closes a trade.
        portfolio_review = _portfolio_review_text()
        if portfolio_review:
            trend += ". " + portfolio_review
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
                         menu=MENU, menu_text=MENU_TEXT, session=session)
        text = text.replace("The list:", f"Market: {trend}.\n\nThe list:", 1)
        try:
            answer = await chat_json(text, gt.schema(list(facts), POLICY.gemma_max_positions, menu=MENU),
                                     client)
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
            "submitted": submit and tradable, "session": session}), POLICY)

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
        # Sector concentration across the account: count all positions per sector
        # (not just Gemma's) so the cap applies account-wide.
        held_sectors: dict[str, int] = {}
        for p in positions:
            sym = str(p.get("symbol") or "")
            sec = sector_of.get(sym, "")
            if sec:
                held_sectors[sec] = held_sectors.get(sec, 0) + 1

        for s in buys:
            sector_gate = sector_concentration(
                {p.get("symbol") for p in positions}, sector_of, s,
                POLICY.max_per_sector,
            )
            if not sector_gate:
                print(f"  {RED}{s:<6} not bought -- {sector_gate.reason}{RESET}")
                bookorders.log("gemma_refused", s, sector_gate.reason, POLICY)
                continue
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
                atr_multiple=POLICY.gemma_menu_value("stop", pl.stop)
                if pl.stop in ("tight", "normal", "wide") else None,
                reward_risk=POLICY.gemma_menu_value("target", pl.target)
                if pl.target in ("none", "2R", "3R", "5R") else None,
                stop_menu_choice=pl.stop,
                target_menu_choice=pl.target,
                size_menu_choice=pl.size,
                session=session)
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
