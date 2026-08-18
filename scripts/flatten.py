"""Close everything before the weekend.

    python scripts/flatten.py                 # dry run, shows the plan
    python scripts/flatten.py --submit        # actually cancels and closes

WHY THIS EXISTS
A stop is an instruction to an exchange that is shut from Friday afternoon to
Monday morning. Whatever happens in between arrives as a gap through the stop,
not a fill at it, and there is nothing to be done about it until the open. This
closes the book before that window opens.

It is not free. Measured on this strategy, going flat before the weekend costs
-0.213R per trade against a validated edge of +0.218R. That is the price of the
protection, and it is the operator's decision to pay it.

ORDER OF OPERATIONS
Cancel working orders first, then close positions. A resting bracket leg
reserves the shares behind it, so a close submitted underneath a live stop can
be rejected for insufficient quantity — and a rejected close looks very much
like a successful one in a log nobody reads until Monday.

SAFE TO RUN TWICE
A retry, a manual run, or a task firing after a partial failure must not throw
or double-submit. Alpaca returns an error for an already-cancelled order and an
already-closed position; both are treated as success, because the desired state
is what matters, not who achieved it.

EXIT CODE IS THE CONTRACT
Non-zero when anything is still open at the end. The scheduled task reports
that code, so a partial failure shows red in Task Scheduler instead of leaving
a position exposed over precisely the weekend this was written to avoid.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

import httpx

from scanner.config import settings
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.audit import append_event
from scanner.execution.flatten import plan_flatten
from scanner.execution.policy import TradingPolicy
from scanner.models import AuditEvent

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

POLICY = TradingPolicy()


async def main(submit: bool) -> int:
    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)

        account = await broker.account()
        print(f"{DIM}paper account: equity {account.equity:,.2f}, "
              f"status {account.status}{RESET}")

        positions = await broker._get("/v2/positions")
        orders = await broker._get("/v2/orders", status="open", limit=500)
        plan = plan_flatten(positions=positions, orders=orders)

        print(f"{BOLD}{plan.describe()}{RESET}")
        if plan.is_empty:
            return 0
        if not submit:
            print(f"{YELLOW}dry run — nothing sent. Pass --submit to act.{RESET}")
            return 0

        failures: list[str] = []

        # 1. Working orders first: a live bracket leg reserves the shares.
        for order_id in plan.cancel_first:
            try:
                await broker.cancel_order(order_id)
                print(f"  {DIM}cancelled {order_id}{RESET}")
            except BrokerError as exc:
                # Already gone is the state we wanted; anything else is real.
                print(f"  {YELLOW}cancel {order_id}: {exc}{RESET}")

        # 2. Then close, at market.
        for symbol in plan.close:
            try:
                await broker.close_position(symbol)
                print(f"  {GREEN}closing {symbol}{RESET}")
            except BrokerError as exc:
                print(f"  {RED}FAILED to close {symbol}: {exc}{RESET}")
                failures.append(symbol)

        # 3. Verify against the BROKER, not against our own intentions.
        await asyncio.sleep(3)
        remaining = await broker._get("/v2/positions")
        still_open = tuple(p["symbol"] for p in remaining if float(p.get("qty") or 0))

        append_event(settings.audit_log_path, AuditEvent(
            event="weekend_flatten", at=datetime.now().astimezone(), symbol="",
            stage="flatten", policy=POLICY.fingerprint,
            reason=(f"cancelled {len(plan.cancel_first)}, closed "
                    f"{len(plan.close) - len(failures)} of {len(plan.close)}"
                    + (f"; STILL OPEN: {', '.join(still_open)}" if still_open else "")),
        ))

        if still_open:
            print(f"\n{RED}{BOLD}STILL OPEN OVER THE WEEKEND: "
                  f"{', '.join(still_open)}{RESET}")
            print(f"{RED}These need closing by hand before the bell.{RESET}")
            return 1

        print(f"\n{GREEN}{BOLD}flat — no open positions{RESET}")
        return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--submit", action="store_true",
                   help="actually cancel and close; omit for a dry run")
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.submit)))
