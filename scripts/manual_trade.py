"""Enter one of the operator's own picks through the system.

    python scripts/manual_trade.py NVDA --stop 172.50                 # preview
    python scripts/manual_trade.py NVDA --stop 172.50 --target 195    # preview
    python scripts/manual_trade.py NVDA --stop 172.50 --submit        # send
    python scripts/manual_trade.py NVDA --stop 172.50 --limit 180 --note "AI capex"

Preview is the default; nothing is sent without --submit.

WHAT IT DOES (WORKLIST §37)
  1  last price -> limit = last + the scanner's 0.30% cushion (or --limit)
  2  target = --target, or the policy's default R:R times the stop distance
  3  guards: account status, daily loss stop, open slots, already held or
     ordered, at most `max_per_sector` positions in one sector
  4  size like the scanner: 1% risk, per-position cap, against the sleeve the
     momentum engine does not claim
  5  GTC bracket with a `man-` id, logged as a TradeIntent(strategy="manual")

After that the system owns the exits: protect.py re-arms a lost stop, the
scorecard marks it, and the weekend flatten and 60-bar limit leave it alone
(they apply to the scanner only -- policy.flatten_strategies).

A preview or submit while the market is shut is allowed: the GTC limit rests
until the open, and the stop and target only exist once it fills.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import opinions
from scanner.config import settings
from scanner.execution.scorecard import read_rows
from scanner.execution import manual
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.audit import append_event
from scanner.execution.guards import RiskLimits, check_account, check_symbol
from scanner.execution import ownership
from scanner.execution.ownership import MANUAL
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id
from scanner.execution.sizing import size_position
from scanner.execution.taper import position_cap_for
from scanner.features.intraday import fetch_intraday, max_range
from scanner.models import AuditEvent, TradeIntent
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

POLICY = TradingPolicy()


def refuse(reason: str) -> int:
    print(f"{RED}{BOLD}NOT SENT{RESET} {RED}— {reason}{RESET}")
    return 1


async def _last_price(symbol: str, client: httpx.AsyncClient) -> float:
    bars = await fetch_intraday(symbol, client, minutes=30, range_=max_range(30))
    if not bars.closes:
        raise manual.ManualError(f"no price data for {symbol}")
    return float(bars.closes[-1])


async def main(a: argparse.Namespace) -> int:
    try:
        symbol = manual.validate_symbol(a.symbol)
    except manual.ManualError as exc:
        return refuse(str(exc))

    async with httpx.AsyncClient(timeout=60) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        raw = await broker._get("/v2/account")
        account = await broker.account()
        positions = await broker._get("/v2/positions")
        open_orders = await broker._get("/v2/orders", status="open", nested="true")
        owner_map = ownership.owners(await broker._get(
            "/v2/orders", status="all", limit=500, nested="true"))
        market_open = await broker.market_open()
        universe = await composite.load_universe(client, COMPOSITE_1500)
        sector_of = {m.symbol: m.sector for m in universe}
        try:
            last = a.limit or await _last_price(symbol, client)
        except Exception as exc:                      # noqa: BLE001
            return refuse(f"cannot price {symbol}: {exc}")

    held = frozenset(p["symbol"] for p in positions)
    # Momentum's book has its own sleeve: it never uses a manual slot or
    # sector place (manual.counted).
    in_use = manual.counted(held, open_orders, owner_map)
    pending = frozenset(o["symbol"] for o in open_orders)

    # --- levels -------------------------------------------------------------
    limit = a.limit or manual.limit_price(last, POLICY.entry_limit_offset_pct)
    try:
        stop, target = manual.resolve_levels(
            limit, a.stop, a.target, a.rr or POLICY.manual_default_reward_risk)
    except manual.ManualError as exc:
        return refuse(str(exc))
    stop_pct = (limit - stop) / limit * 100
    reward_risk = (target - limit) / (limit - stop)

    # --- guards -------------------------------------------------------------
    limits = RiskLimits(max_positions=POLICY.max_positions,
                        max_daily_loss_pct=POLICY.max_daily_loss_pct,
                        min_equity=POLICY.min_equity,
                        max_position_pct=POLICY.max_position_pct,
                        risk_pct=POLICY.risk_pct)
    slots_used = len(in_use)
    gate = check_account(
        account.equity, float(raw.get("last_equity") or account.equity),
        slots_used, limits, market_open=True,      # a GTC limit may rest overnight
        account_status=account.status, account_blocked=account.blocked)
    if not gate:
        return refuse(gate.reason)
    symbol_gate = check_symbol(symbol, held, pending)
    if not symbol_gate:
        return refuse(symbol_gate.reason)

    sector = a.sector or sector_of.get(symbol, "")
    counts = manual.sector_counts(in_use, sector_of)
    sector_gate = manual.check_sector(sector, counts, POLICY.max_per_sector)
    if not sector_gate:
        return refuse(sector_gate.reason)

    # --- size ---------------------------------------------------------------
    sleeve = manual.sleeve_equity(account.equity, POLICY.own_sleeves_pct)
    cap = min(POLICY.max_position_pct, position_cap_for(date.today(), POLICY))
    sizing = size_position(sleeve, limit, stop, risk_pct=POLICY.risk_pct,
                           max_position_pct=cap,
                           buying_power=min(account.buying_power, sleeve))
    if not sizing.is_tradeable:
        return refuse(sizing.limited_by)

    now = datetime.now().astimezone()
    try:
        payload = manual.bracket(symbol, sizing.shares, limit=limit, stop=stop,
                                 target=target, at=now)
    except manual.ManualError as exc:
        return refuse(str(exc))

    print(f"\n{BOLD}{symbol}{RESET} {DIM}{sector or 'sector unknown'}{RESET}"
          f"   last {last:,.2f}")
    print(f"  {sizing.shares} sh, limit {limit:,.2f}   stop {RED}{stop:,.2f}{RESET} "
          f"({stop_pct:.2f}% below)   target {GREEN}{target:,.2f}{RESET}   "
          f"{CYAN}{reward_risk:.2f}R{RESET}")
    print(f"  {DIM}risking {sizing.risk_dollars:,.2f} "
          f"({sizing.risk_dollars / account.equity * 100:.2f}% of equity), "
          f"position {sizing.position_dollars:,.2f}, limited by {sizing.limited_by}; "
          f"sleeve {sleeve:,.0f} of {account.equity:,.0f}{RESET}")
    print(f"  {DIM}slots {slots_used + 1}/{POLICY.max_positions}"
          + (f", {sector} {counts.get(sector, 0) + 1}/{POLICY.max_per_sector}"
             if sector and POLICY.max_per_sector else "") + RESET)
    if not sector:
        print(f"  {YELLOW}sector unknown (not in the S&P 1500) -- the sector cap "
              f"cannot count it; pass --sector to include it{RESET}")
    if stop_pct < POLICY.min_risk_pct:
        print(f"  {YELLOW}stop is {stop_pct:.2f}% away; the scanner refuses under "
              f"{POLICY.min_risk_pct}% because costs eat tight stops{RESET}")
    if a.target is None:
        print(f"  {DIM}target from the default {a.rr or POLICY.manual_default_reward_risk}R "
              f"-- a chosen number, not a measured one{RESET}")
    if not market_open:
        print(f"  {YELLOW}market is closed: the limit rests until the open{RESET}")

    # SECOND OPINIONS, shown and recorded, never acted on (WORKLIST §38). The
    # snapshot lets a later question -- did following TradingAgents help? --
    # be answered from data rather than memory.
    _, picks, _ = opinions.momentum_picks()
    snap = opinions.snapshot(symbol, read_rows(settings.audit_log_path), picks)
    calls = ", ".join(f"{k} {c['call']} ({c['at']})" for k, c in snap["opinions"].items())
    print(f"  {DIM}second opinions (advisory): momentum {snap['momentum']}"
          f"{'; ' + calls if calls else '; none on record'}{RESET}")
    if snap["disagrees_with_primary"]:
        print(f"  {YELLOW}{', '.join(snap['disagrees_with_primary'])} disagrees with the "
              f"momentum rule on {symbol} -- unproven, see second_opinion.py{RESET}")

    intent = TradeIntent(
        symbol=symbol, action="buy", strategy=MANUAL, trigger_at=now,
        entry_price=limit, stop_price=stop, target_price=target,
        shares=sizing.shares, risk_dollars=sizing.risk_dollars,
        position_dollars=sizing.position_dollars, reward_risk=round(reward_risk, 3),
        paper=True, dry_run=not a.submit, client_order_id=payload["client_order_id"],
        sector=sector, llm_concern=a.note or "",
    )
    if not a.submit:
        print(f"\n{YELLOW}preview only -- nothing sent or logged; "
              f"re-run with --submit{RESET}")
        return 0
    append_event(settings.audit_log_path, AuditEvent(
        event="trade_intent", at=now, symbol=symbol, intent=intent,
        stage="trade", reason=a.note or "manual pick",
        policy=POLICY.fingerprint, build=build_id(),
    ))

    async with httpx.AsyncClient(timeout=60) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        try:
            order = await broker.submit(payload)
        except BrokerError as exc:
            append_event(settings.audit_log_path, AuditEvent(
                event="order_rejected", at=datetime.now().astimezone(),
                symbol=symbol, intent=intent, request_id=broker.last_request_id,
                reason=str(exc), stage="trade",
                policy=POLICY.fingerprint, build=build_id()))
            return refuse(f"broker: {exc}")
        append_event(settings.audit_log_path, AuditEvent(
            event="order_submitted", at=datetime.now().astimezone(),
            symbol=symbol, intent=intent, order_id=str(order.get("id", "")),
            request_id=broker.last_request_id, stage="trade",
            reason=a.note or "manual pick",
            policy=POLICY.fingerprint, build=build_id()))
        append_event(settings.audit_log_path, AuditEvent(
            event="second_opinion_snapshot", at=datetime.now().astimezone(),
            symbol=symbol, order_id=str(order.get("id", "")), stage="advisor",
            reason=json.dumps({**snap, "client_order_id": payload["client_order_id"]}),
            policy=POLICY.fingerprint, build=build_id()))
    print(f"\n{GREEN}{BOLD}submitted{RESET} {DIM}id {order.get('id', '?')}, "
          f"client id {payload['client_order_id']}{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbol")
    p.add_argument("--stop", type=float, required=True,
                   help="protective stop price (required: size comes from it)")
    p.add_argument("--target", type=float, default=None)
    p.add_argument("--rr", type=float, default=None,
                   help=f"target as a multiple of the stop distance "
                        f"(default {POLICY.manual_default_reward_risk})")
    p.add_argument("--limit", type=float, default=None,
                   help="entry limit price (default: last + 0.30%%)")
    p.add_argument("--sector", default="", help="override the sector label")
    p.add_argument("--note", default="", help="why: recorded with the trade")
    p.add_argument("--submit", action="store_true", help="actually send it")
    raise SystemExit(asyncio.run(main(p.parse_args())))
