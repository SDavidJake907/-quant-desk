"""What was sent, what filled, what is open, and what it is worth.

    python scripts/positions.py              # full picture
    python scripts/positions.py --history    # + equity curve
    python scripts/positions.py --json       # machine readable

Read-only. This script never places, cancels or modifies an order.

It joins the audit log to the broker's own record on `client_order_id`, so
every intent can be traced to an outcome — and anything the broker knows about
that the log does not is reported rather than hidden.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from scanner.config import settings
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.reconcile import (
    CLOSED_FLAT,
    CLOSED_OTHER,
    CLOSED_STOP,
    CLOSED_TARGET,
    EXPIRED,
    FILLED_OPEN,
    REJECTED,
    WORKING,
    Summary,
    reconcile,
)

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

STATE_LABEL = {
    WORKING: (YELLOW, "working"),
    FILLED_OPEN: (CYAN, "open"),
    CLOSED_TARGET: (GREEN, "target hit"),
    CLOSED_STOP: (RED, "stopped"),
    CLOSED_OTHER: (DIM, "closed"),
    CLOSED_FLAT: (CYAN, "flattened"),
    EXPIRED: (DIM, "expired"),
    REJECTED: (RED, "rejected"),
}

#: A state this report has never heard of must not take the report down. It
#: was CLOSED_OTHER that was missing before CLOSED_FLAT existed, and the first
#: non-bracket close would have crashed a script the operator runs to find out
#: what the account is doing.
UNKNOWN_STATE = (YELLOW, "?")


def load_intents(path: str | Path) -> dict[str, dict]:
    """Latest intent per client_order_id from the append-only audit log."""
    target = Path(path)
    if not target.exists():
        return {}
    intents: dict[str, dict] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        intent = event.get("intent") or {}
        coid = intent.get("client_order_id")
        if coid:
            intents[coid] = intent
    return intents


def _money(value: float) -> str:
    return f"{value:,.2f}"


async def main(show_history: bool, as_json: bool, limit: int) -> None:
    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        account = await broker.account()
        raw_account = await broker._get("/v2/account")
        orders = await broker._get("/v2/orders", status="all", nested="true",
                                   limit=limit, direction="desc")
        positions = await broker._get("/v2/positions")
        history = None
        if show_history:
            history = await broker._get("/v2/account/portfolio/history",
                                        period="1M", timeframe="1D")

    # Live protective legs, per symbol. Only OPEN orders count — a filled or
    # cancelled stop protects nothing.
    # Each leg is judged on ITS OWN status, never the parent's. A filled
    # bracket parent still carries live protective legs — that is the normal
    # state of a working position — and skipping them because the parent reads
    # "filled" reported Q as unprotected while its stop sat armed at 133.59.
    #
    # `held` counts as live: it is the OCO state Alpaca parks the stop in while
    # the target is working. Treating it as inactive is the same error.
    LIVE = ("new", "accepted", "held", "partially_filled", "pending_new")
    protection: dict[str, dict[str, float]] = {}
    for order in orders:
        for leg in [order] + list(order.get("legs") or []):
            symbol = leg.get("symbol")
            if (not symbol or leg.get("side") != "sell"
                    or leg.get("status") not in LIVE):
                continue
            entry = protection.setdefault(symbol, {})
            if leg.get("stop_price"):
                entry["stop"] = float(leg["stop_price"])
            elif leg.get("limit_price"):
                entry["target"] = float(leg["limit_price"])

    marks = {p["symbol"]: float(p["current_price"]) for p in positions}
    trades = reconcile(load_intents(settings.audit_log_path), orders)
    summary = Summary(trades=trades)

    last_equity = float(raw_account.get("last_equity") or account.equity)
    day_pl = account.equity - last_equity

    if as_json:
        print(json.dumps({
            "account": {"equity": account.equity, "cash": account.cash,
                        "buying_power": account.buying_power,
                        "day_pl": day_pl, "status": account.status},
            "summary": {"sent": summary.sent, "filled": summary.filled,
                        "fill_rate": summary.fill_rate,
                        "working": summary.working,
                        "open": summary.open_positions,
                        "closed": len(summary.closed),
                        "realized_r": summary.realized_r,
                        "expectancy_r": summary.expectancy},
            "positions": positions,
            "trades": [t.model_dump(mode="json") for t in trades],
        }, indent=2, default=str))
        return

    # ---- account ---------------------------------------------------------
    colour = GREEN if day_pl >= 0 else RED
    print(f"\n{BOLD}ACCOUNT{RESET} {DIM}(paper){RESET}")
    print(f"  equity          {_money(account.equity):>14}   "
          f"today {colour}{day_pl:+,.2f}{RESET} "
          f"({colour}{day_pl / last_equity * 100 if last_equity else 0:+.2f}%{RESET})")
    print(f"  cash            {_money(account.cash):>14}")
    print(f"  buying power    {_money(account.buying_power):>14}")
    print(f"  status          {account.status:>14}"
          f"{'' if account.tradeable else f'  {RED}NOT TRADEABLE{RESET}'}")

    # ---- open positions --------------------------------------------------
    print(f"\n{BOLD}POSITIONS{RESET} {DIM}({len(positions)} open){RESET}")
    if not positions:
        print(f"  {DIM}none{RESET}")
    for p in positions:
        pl = float(p["unrealized_pl"])
        side = p["side"]
        c = GREEN if pl >= 0 else RED
        trade = next((t for t in trades
                      if t.symbol == p["symbol"] and t.state == FILLED_OPEN), None)
        r = trade.r_multiple(marks.get(p["symbol"])) if trade else None
        print(f"  {BOLD}{p['symbol']:<6}{RESET} {side:<5} {float(p['qty']):>7,.0f} sh  "
              f"@ {float(p['avg_entry_price']):>9,.2f}  now {float(p['current_price']):>9,.2f}  "
              f"{c}{pl:>+10,.2f}{RESET} ({c}{float(p['unrealized_plpc']) * 100:+.2f}%{RESET})"
              f"{f'   {r:+.2f}R' if r is not None else ''}")
        # Protection is read from the BROKER, not from the intent. Showing the
        # intended stop for a position whose bracket legs were cancelled says
        # "protected" about a naked position — which is how SUPN sat unhedged
        # at -0.93R with 418 shares and this screen showed stop 46.56.
        legs = protection.get(p["symbol"], {})
        if legs.get("stop") or legs.get("target"):
            stop = f"{legs['stop']:,.2f}" if legs.get("stop") else f"{RED}NONE{RESET}"
            target = f"{legs['target']:,.2f}" if legs.get("target") else f"{RED}NONE{RESET}"
            print(f"         {DIM}stop {stop} · target {target} · "
                  f"market value {float(p['market_value']):,.2f}{RESET}")
        else:
            print(f"         {RED}NO STOP, NO TARGET — position is unprotected"
                  f"{RESET} {DIM}· market value {float(p['market_value']):,.2f}{RESET}")
        if trade and (legs.get("stop") or 0) != round(trade.intended_stop, 2):
            print(f"         {DIM}intended stop was {trade.intended_stop:,.2f}{RESET}")

    # ---- orders ----------------------------------------------------------
    working = [t for t in trades if t.state == WORKING]
    print(f"\n{BOLD}WORKING ORDERS{RESET} {DIM}({len(working)} sent, not filled){RESET}")
    if not working:
        print(f"  {DIM}none{RESET}")
    for t in working:
        print(f"  {BOLD}{t.symbol:<6}{RESET} {t.shares:>7} sh  "
              f"limit {t.intended_entry:>9,.2f}  "
              f"{DIM}stop {t.intended_stop:,.2f} · target {t.intended_target:,.2f}{RESET}")

    # ---- closed ----------------------------------------------------------
    closed = summary.closed
    print(f"\n{BOLD}CLOSED{RESET} {DIM}({len(closed)}){RESET}")
    if not closed:
        print(f"  {DIM}none yet{RESET}")
    for t in closed:
        colour, label = STATE_LABEL.get(t.state, UNKNOWN_STATE)
        r = t.r_multiple()
        print(f"  {BOLD}{t.symbol:<6}{RESET} {colour}{label:<11}{RESET} "
              f"in {t.entry_fill:>9,.2f}  out {t.exit_fill:>9,.2f}  "
              f"{GREEN if (r or 0) > 0 else RED}{r:+.2f}R{RESET}")

    # ---- the reconciliation ---------------------------------------------
    print(f"\n{BOLD}RUN TOTALS{RESET}")
    print(f"  sent {summary.sent}   filled {summary.filled}   "
          f"working {summary.working}   open {summary.open_positions}   "
          f"closed {len(closed)}")
    print(f"  {BOLD}fill rate {summary.fill_rate:.1f}%{RESET} "
          f"{DIM}<- the number the backtest could not measure{RESET}")
    if summary.untracked:
        names = ", ".join(sorted({t.symbol for t in summary.untracked}))
        print(f"  {YELLOW}{len(summary.untracked)} untracked order(s){RESET} "
              f"{DIM}({names}) — opened outside this system, excluded below{RESET}")
    if summary.filled:
        print(f"  mean slippage {summary.mean_slippage:+.4f} per share "
              f"{DIM}(fill minus intended entry){RESET}")
        gap = summary.mean_stop_fill_gap
        if gap:
            flag = f"  {YELLOW}<- paper fills are optimistic{RESET}" if gap > 0 else ""
            print(f"  mean stop fill {gap:+.4f} vs the stop level{flag}")
    if closed:
        c = GREEN if summary.expectancy > 0 else RED
        print(f"  realized {c}{summary.realized_r:+.2f}R{RESET} over {len(closed)} "
              f"closed   expectancy {c}{summary.expectancy:+.3f}R{RESET}")
        print(f"  {DIM}backtest holdout expectancy was +0.218R over 75 trades{RESET}")
    else:
        print(f"  {DIM}no closed trades yet — nothing to compare to the backtest{RESET}")

    if history and history.get("equity"):
        points = [e for e in history["equity"] if e]
        if len(points) > 1:
            change = (points[-1] - points[0]) / points[0] * 100
            c = GREEN if change >= 0 else RED
            print(f"\n{BOLD}EQUITY, LAST MONTH{RESET}")
            print(f"  {points[0]:,.2f} -> {points[-1]:,.2f}   "
                  f"{c}{change:+.2f}%{RESET}   "
                  f"{DIM}peak {max(points):,.2f} · trough {min(points):,.2f}{RESET}")

    print(f"\n{DIM}Read-only. This script never places, cancels or modifies an order.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--history", action="store_true", help="include the equity curve")
    p.add_argument("--json", action="store_true", dest="as_json")
    p.add_argument("--limit", type=int, default=200, help="orders to pull back")
    a = p.parse_args()
    try:
        asyncio.run(main(a.history, a.as_json, a.limit))
    except BrokerError as exc:
        raise SystemExit(f"broker unavailable: {exc}")
