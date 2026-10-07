"""Gemma's book: the junior trader's morning decision (WORKLIST §42).

    python scripts/gemma_trade.py            # ask Gemma, show the decision, send nothing
    python scripts/gemma_trade.py --submit   # ask, then trade it (05:45 AK task)

1  The list: watchlists/core.txt + watchlists/gemma.txt, minus names another book holds or has ordered,
   minus watchlists/picks.txt and momentum's current picks (reserved even
   before they are bought), plus whatever Gemma's book holds now.
2  Facts per name, computed here (Gemma writes no numbers): 1/3/12-month and
   12-1 momentum returns, daily ATR, the S&P trend, earnings within 7 days,
   recent headlines, TradingAgents' latest call.
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
from datetime import date

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import gemma_trader as gt
from scanner.advisor import opinions
from scanner.advisor.scan_watch import operator_names
from scanner.config import settings
from scanner.execution import bookorders, ownership, picks
from scanner.execution.alpaca import Alpaca
from scanner.execution.guards import RiskLimits, check_account
from scanner.execution.ownership import GEMMA
from scanner.execution.policy import TradingPolicy
from scanner.execution.safety import kill_switch_active
from scanner.execution.scorecard import read_rows
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday_context import atr_pct
from scanner.features.technicals import compute
from scanner.llm.ollama import LLMError, chat_json
from scanner.sources import calendar as event_calendar
from scanner.sources import yahoo_rss
from pathlib import Path

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"
POLICY = TradingPolicy()
CORE = Path("watchlists/core.txt")
GEMMA_LIST = Path("watchlists/gemma.txt")


def _ret(closes, back: int) -> str:
    return f"{(closes[-1] / closes[-back - 1] - 1) * 100:+.1f}%" if len(closes) > back else "n/a"


async def facts_for(symbols, client) -> tuple[dict[str, str], str]:
    daily, _ = await fetch_many(tuple(symbols), client, range_="2y")
    spx = await fetch_bars("^GSPC", client, range_="2y")
    sma = sum(spx.closes[-210:]) / 210
    trend = (f"S&P 500 {spx.closes[-1]:,.0f} is {'ABOVE' if spx.closes[-1] > sma else 'BELOW'} "
             f"its 210-day average {sma:,.0f}")
    try:
        earnings = await event_calendar.upcoming_earnings(frozenset(symbols), client)
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
        lines = [f"{s} | 1m {_ret(b.closes, 21)} | 3m {_ret(b.closes, 63)} | "
                 f"12m {_ret(b.closes, 252)} | 12-1 momentum {mom} | "
                 f"daily ATR {atr:.1f}%" if atr else "daily ATR n/a"]
        report = earnings.get(s)
        lines.append(f"earnings: {report.label}" if report else "earnings: none in the next 7 days")
        ta = opinions.latest_calls(rows, s).get("tradingagents")
        if ta:
            lines.append(f"TradingAgents (unproven) said {ta['call']} on {ta['at']}")
        try:
            items = await yahoo_rss.fetch(s, client)
            for item in items[:5]:
                lines.append(f"headline: {item.title}")
        except Exception:                                # noqa: BLE001
            lines.append("headlines: unavailable")
        out[s] = "\n".join(lines)
    return out, trend


async def main(submit: bool) -> int:
    async with httpx.AsyncClient(timeout=120) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        raw = await broker._get("/v2/account")
        account = await broker.account()
        positions = await broker._get("/v2/positions")
        open_orders = await broker._get("/v2/orders", status="open", nested="true")
        owner_map = ownership.owners(await broker._get(
            "/v2/orders", status="all", limit=500, nested="true"))
        market_open = await broker.market_open()

        mine = {p["symbol"]: p for p in positions
                if ownership.owner(p["symbol"], owner_map) == GEMMA}
        taken = {p["symbol"] for p in positions if p["symbol"] not in mine}
        taken |= {o["symbol"] for o in open_orders
                  if ownership.strategy_of(o.get("client_order_id")) != GEMMA}
        # Reserved for other books even before they buy: the operator's picks
        # list trades at 05:33, momentum at 11:40 on rebalance days. Momentum is
        # the one tested rule; letting Gemma take its names first would quietly
        # change what is being tested.
        taken |= set(picks.read_list()[0]) | set(opinions.momentum_picks()[1])
        universe = list(dict.fromkeys((*operator_names(CORE), *operator_names(GEMMA_LIST))))
        allowed = [s for s in universe if s not in taken]
        allowed += [s for s in mine if s not in allowed]
        print(f"{BOLD}Gemma's book{RESET}  sleeve {POLICY.gemma_allocation_pct:.0f}% "
              f"(${account.equity * POLICY.gemma_allocation_pct / 100:,.0f}), "
              f"up to {POLICY.gemma_max_positions} names")
        print(f"  holds {sorted(mine) or 'nothing'}; may choose from {allowed}")
        if not allowed:
            print("  nothing on the list is free -- no decision today")
            return 0

        facts, trend = await facts_for(allowed, client)
        held_status = {s: f"{float(p.get('unrealized_plpc') or 0) * 100:+.1f}% since entry"
                       for s, p in mine.items()}
        text = gt.prompt(facts, held=held_status, max_positions=POLICY.gemma_max_positions)
        text = text.replace("The list:", f"Market: {trend}.\n\nThe list:", 1)
        try:
            answer = await chat_json(text, gt.schema(list(facts), POLICY.gemma_max_positions),
                                     client)
        except LLMError as exc:
            answer = None
            print(f"  {RED}Gemma unavailable: {exc} -- book left unchanged{RESET}")
        decision = gt.validate(answer, facts, held=set(mine),
                               max_positions=POLICY.gemma_max_positions)
        buys, sells = gt.diff(decision.keep, mine)
        bookorders.log("gemma_decision", "", json.dumps({
            "keep": decision.keep, "buys": buys, "sells": sells,
            "reasons": dict(decision.reasons), "rejected": dict(decision.rejected),
            "journal": decision.journal, "unchanged": decision.unchanged,
            "submitted": submit and market_open}), POLICY)

        print(f"\n  {BOLD}Gemma holds{RESET} {list(decision.keep) or 'nothing'}")
        for s, why in decision.reasons:
            print(f"    {s:<6} {why}")
        for s, why in decision.rejected:
            print(f"    {DIM}{s:<6} not taken -- {why}{RESET}")
        if decision.journal:
            print(f"  {DIM}journal: {decision.journal}{RESET}")
        print(f"  plan: buy {list(buys) or 'nothing'}; sell {list(sells) or 'nothing'}")

        if not market_open:
            print(f"  {DIM}market closed -- nothing sent{RESET}")
            return 0
        if not submit:
            print(f"  {YELLOW}preview -- re-run with --submit{RESET}")
            return 0

        for s in sells:
            ok = await bookorders.sell(broker, s, open_orders, strategy=GEMMA,
                                       reason="Gemma dropped it", policy=POLICY)
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
            result = await bookorders.buy_with_stop(
                broker, client, s, strategy=GEMMA, sleeve=sleeve, room=room, cap_pct=cap,
                policy=POLICY, reason=f"Gemma: {reasons.get(s, '')}")
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
