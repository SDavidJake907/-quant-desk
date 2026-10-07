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
from scanner.execution import bookorders, manual, ownership, picks
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.guards import RiskLimits, check_account
from scanner.execution.ownership import PICKS
from scanner.execution.policy import TradingPolicy
from scanner.execution.safety import kill_switch_active

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"
POLICY = TradingPolicy()


def log(event: str, symbol: str, reason: str, **kw) -> None:
    bookorders.log(event, symbol, reason, POLICY, **kw)


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
            ok = await bookorders.sell(broker, symbol, open_orders, strategy=PICKS,
                                       reason="removed from the list", policy=POLICY)
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
        sleeve = manual.sleeve_equity(account.equity, POLICY.own_sleeves_pct)
        deployed = sum(float(p.get("market_value") or 0) for p in positions
                       if ownership.owner(p["symbol"], owner_map) not in ownership.OWN_SLEEVE
                       and p["symbol"] not in plan.sells)
        room = max(0.0, min(account.buying_power, sleeve - deployed))
        cap = min(POLICY.max_position_pct, POLICY.shared_position_cap_pct)
        for symbol in plan.buys:
            gate = check_account(account.equity, float(raw.get("last_equity") or account.equity),
                                 len(in_use), limits, market_open=True,
                                 account_status=account.status, account_blocked=account.blocked)
            if not gate:
                print(f"  {YELLOW}{symbol:<6} not bought -- {gate.reason}{RESET}")
                log("picks_refused", symbol, gate.reason)
                continue
            result = await bookorders.buy_with_stop(
                broker, client, symbol, strategy=PICKS, sleeve=sleeve, room=room,
                cap_pct=cap, policy=POLICY, reason="on the picks list")
            if not result.ok:
                print(f"  {YELLOW}{symbol:<6} not bought -- {result.message}{RESET}")
                continue
            in_use.add(symbol)
            room -= result.sizing.position_dollars
            print(f"  {GREEN}bought{RESET} {symbol:<6} {result.message}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--submit", action="store_true", help="actually trade")
    raise SystemExit(asyncio.run(main(p.parse_args().submit)))
