"""Keep the account in step with watchlists/picks.txt (WORKLIST §41).

    python scripts/picks_sync.py            # show what it would do
    python scripts/picks_sync.py --submit   # do it (the 30-minute safety task runs this)

Each run, in market hours:
  1  cancel picks buys that sat unfilled past `picks_stale_entry_minutes`
  2  SELL any picks position whose name left the list (legs cancelled first)
  3  BUY any listed name nobody holds: limit at last + 0.30%, safety stop
     `picks_stop_atr_multiple` daily ATRs below, target ceiling, GTC bracket,
     sized like manual picks (1% risk, per-position cap, the non-momentum half)

Guards: market open, account status, daily loss stop, kill switch (blocks
buys, never sells), slots shared with manual trades. A listed name another
strategy holds is skipped and named. Every order is logged as a TradeIntent
with strategy "picks", so protect.py re-arms its stop and the scorecard
scores it apart from momentum and one-off manual trades.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timezone

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution import manual, ownership, picks
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.audit import append_event
from scanner.execution.flatten_run import close_when_released
from scanner.execution.guards import RiskLimits, check_account
from scanner.execution.ownership import PICKS
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id
from scanner.execution.safety import kill_switch_active
from scanner.execution.sizing import size_position
from scanner.execution.taper import position_cap_for
from scanner.features.bars import fetch_bars
from scanner.features.intraday import fetch_intraday, max_range
from scanner.features.intraday_context import atr_pct
from scanner.models import AuditEvent, TradeIntent

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"
POLICY = TradingPolicy()


def log(event: str, symbol: str, reason: str, **kw) -> None:
    append_event(settings.audit_log_path, AuditEvent(
        event=event, at=datetime.now().astimezone(), symbol=symbol, reason=reason,
        stage="trade", policy=POLICY.fingerprint, build=build_id(), **kw))


async def _price_and_atr(symbol: str, client) -> tuple[float, float]:
    intraday = await fetch_intraday(symbol, client, minutes=30, range_=max_range(30))
    daily = await fetch_bars(symbol, client, range_="6mo")
    last = float(intraday.closes[-1]) if intraday.closes else float(daily.closes[-1])
    return last, atr_pct(daily) or 0.0


async def main(submit: bool) -> int:
    listed, rejected = picks.read_list()
    print(f"{BOLD}picks{RESET} {', '.join(listed) or '(list empty)'}")
    if rejected:
        print(f"  {YELLOW}not stock tickers, ignored: {', '.join(rejected)}{RESET}")

    async with httpx.AsyncClient(timeout=60) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        raw = await broker._get("/v2/account")
        account = await broker.account()
        positions = await broker._get("/v2/positions")
        open_orders = await broker._get("/v2/orders", status="open", nested="true")
        owner_map = ownership.owners(await broker._get(
            "/v2/orders", status="all", limit=500, nested="true"))
        market_open = await broker.market_open()

        held = {p["symbol"] for p in positions}
        pending = {o["symbol"] for o in open_orders if o.get("side") == "buy"}
        stale = picks.stale_entries(open_orders, datetime.now(timezone.utc),
                                    minutes=POLICY.picks_stale_entry_minutes)
        stale_syms = {o["symbol"] for o in stale}
        plan = picks.plan(listed, owner_map, held, pending - stale_syms)
        for symbol, why in plan.skipped:
            print(f"  {DIM}{symbol:<6} skipped -- {why}{RESET}")
        print(f"  plan: buy {list(plan.buys) or 'nothing'}; sell {list(plan.sells) or 'nothing'}"
              + (f"; re-send stale {sorted(stale_syms)}" if stale_syms else ""))

        if not market_open:
            print(f"  {DIM}market closed -- nothing sent; the first run after the open acts{RESET}")
            return 0
        if not submit:
            print(f"  {YELLOW}preview -- re-run with --submit{RESET}")
            return 0

        # 1. stale entries
        for order in stale:
            try:
                await broker.cancel_order(str(order["id"]))
                log("picks_entry_cancelled", order["symbol"], "unfilled past the stale limit; re-sending")
            except BrokerError as exc:
                print(f"  {RED}cancel {order['symbol']}: {exc}{RESET}")

        # 2. sells: cancel that symbol's legs, then close once the shares are free
        for symbol in plan.sells:
            for order in open_orders:
                if order.get("symbol") == symbol:
                    try:
                        await broker.cancel_order(str(order["id"]))
                    except BrokerError:
                        pass
            failed = await close_when_released(broker, (symbol,))
            ok = symbol not in failed
            log("picks_exit", symbol, "removed from the list; " +
                ("closed at market" if ok else f"CLOSE FAILED: {failed.get(symbol)}"))
            print(f"  {GREEN if ok else RED}{'sold' if ok else 'SELL FAILED'}{RESET} {symbol}")

        # 3. buys
        if kill_switch_active(settings.kill_switch_path):
            print(f"  {RED}kill switch active -- no buys{RESET}")
            return 0
        limits = RiskLimits(max_positions=POLICY.max_positions,
                            max_daily_loss_pct=POLICY.max_daily_loss_pct,
                            min_equity=POLICY.min_equity,
                            max_position_pct=POLICY.max_position_pct, risk_pct=POLICY.risk_pct)
        in_use = set(manual.counted(held - set(plan.sells), open_orders, owner_map))
        sleeve = manual.sleeve_equity(account.equity, POLICY.momentum_allocation_pct)
        deployed = sum(float(p.get("market_value") or 0) for p in positions
                       if ownership.owner(p["symbol"], owner_map) != ownership.MOMENTUM
                       and p["symbol"] not in plan.sells)
        room = max(0.0, min(account.buying_power, sleeve - deployed))
        cap = min(POLICY.max_position_pct, position_cap_for(date.today(), POLICY))
        for symbol in plan.buys:
            gate = check_account(account.equity, float(raw.get("last_equity") or account.equity),
                                 len(in_use), limits, market_open=True,
                                 account_status=account.status, account_blocked=account.blocked)
            if not gate:
                print(f"  {YELLOW}{symbol:<6} not bought -- {gate.reason}{RESET}")
                log("picks_refused", symbol, gate.reason)
                continue
            try:
                last, atr = await _price_and_atr(symbol, client)
                limit, stop, target = picks.levels(
                    last=last, atr_pct=atr, atr_multiple=POLICY.picks_stop_atr_multiple,
                    offset_pct=POLICY.entry_limit_offset_pct, ceiling=POLICY.picks_target_ceiling)
            except Exception as exc:                       # noqa: BLE001
                print(f"  {RED}{symbol:<6} cannot price: {exc}{RESET}")
                log("picks_refused", symbol, f"cannot price: {exc}")
                continue
            sizing = size_position(sleeve, limit, stop, risk_pct=POLICY.risk_pct,
                                   max_position_pct=cap, buying_power=room)
            if not sizing.is_tradeable:
                print(f"  {YELLOW}{symbol:<6} not bought -- {sizing.limited_by}{RESET}")
                log("picks_refused", symbol, sizing.limited_by)
                continue
            now = datetime.now().astimezone()
            payload = manual.bracket(symbol, sizing.shares, limit=limit, stop=stop,
                                     target=target, at=now)
            payload["client_order_id"] = picks.client_order_id(symbol, now)
            intent = TradeIntent(
                symbol=symbol, action="buy", strategy=PICKS, trigger_at=now,
                entry_price=limit, stop_price=stop, target_price=target,
                shares=sizing.shares, risk_dollars=sizing.risk_dollars,
                position_dollars=sizing.position_dollars,
                reward_risk=round((target - limit) / (limit - stop), 3),
                paper=True, client_order_id=payload["client_order_id"])
            log("trade_intent", symbol, "on the picks list", intent=intent)
            try:
                order = await broker.submit(payload)
            except BrokerError as exc:
                log("order_rejected", symbol, str(exc), intent=intent)
                print(f"  {RED}{symbol:<6} rejected: {exc}{RESET}")
                continue
            log("order_submitted", symbol, "on the picks list", intent=intent,
                order_id=str(order.get("id", "")))
            in_use.add(symbol)
            room -= sizing.position_dollars
            print(f"  {GREEN}bought{RESET} {symbol:<6} {sizing.shares} sh limit {limit:,.2f} "
                  f"stop {stop:,.2f} ({(limit - stop) / limit * 100:.1f}% below, "
                  f"{POLICY.picks_stop_atr_multiple} ATR)  {DIM}risk {sizing.risk_dollars:,.0f}, "
                  f"limited by {sizing.limited_by}{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--submit", action="store_true", help="actually trade")
    raise SystemExit(asyncio.run(main(p.parse_args().submit)))
