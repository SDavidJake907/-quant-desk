"""Close everything before the weekend.

    python scripts/flatten.py                          # dry run, shows the plan
    python scripts/flatten.py --submit                 # cancel and close
    python scripts/flatten.py --submit --only-if-closing-within 20

WHY THIS EXISTS
A stop is an instruction to an exchange that is shut from Friday afternoon to
Monday morning. Whatever happens in between arrives as a gap through the stop,
not a fill at it. This closes the book before that window opens. It costs
-0.213R per trade against a +0.218R validated edge; that is the price of the
protection and it was a deliberate choice.

THE EARLY-CLOSE PROBLEM
US markets close at 13:00 ET on a few days a year. A task pinned to 15:45 ET
fires nearly three hours after the bell on those days and can flatten nothing.
So the session end is read from Alpaca's calendar and `--only-if-closing-within`
lets several triggers share the job: each wakes, asks whether the bell is close,
and the one in the window acts. The rest exit quietly.

ORDER OF OPERATIONS
Cancel working orders first, then close. A resting bracket leg reserves the
shares behind it, so a close submitted under a live stop can be rejected for
insufficient quantity — and a rejected close reads like a successful one.
Unfilled entries are cancelled too: left resting, they can fill on Monday into
a book meant to be empty.

QUANTITIES COME FROM THE BROKER
Positions are closed with DELETE /v2/positions/{symbol}, which closes what is
actually held. Nothing here derives a quantity from local state, so a partial
fill cannot leave a residue and a second run cannot open a short.

VERIFICATION IS A POLL, NOT A GLANCE
Cancels and closes are asynchronous. A single check three seconds later can see
a stale book and report success over a position that never closed. This polls
until the broker reports flat or the deadline passes, and exits non-zero if
anything remains.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

import httpx

from scanner.config import settings
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.audit import append_event
from scanner.execution.flatten import closing_within, is_flat, plan_flatten
from scanner.execution.flatten_run import close_when_released
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id
from scanner.models import AuditEvent

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

POLICY = TradingPolicy()

#: Seconds to keep asking the broker whether the book is empty.
VERIFY_SECONDS = 45
VERIFY_INTERVAL = 5


async def _session_close(broker: Alpaca) -> datetime | None:
    """Today's closing bell from the broker calendar, or None if no session."""
    today = datetime.now().astimezone().date().isoformat()
    try:
        days = await broker._get("/v2/calendar", start=today, end=today)
    except BrokerError:
        return None
    for day in days or []:
        if day.get("date") != today:
            continue
        stamp = f"{day['date']} {day.get('close', '16:00')}"
        try:
            from scanner.features.intraday import EXCHANGE_TZ
            return datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(
                tzinfo=EXCHANGE_TZ)
        except ValueError:
            return None
    return None


async def main(submit: bool, only_within: int | None) -> int:
    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        account = await broker.account()
        close_at = await _session_close(broker)
        now = datetime.now().astimezone()

        if close_at is None:
            print(f"{DIM}no session today per the broker calendar{RESET}")
        else:
            print(f"{DIM}session closes {close_at:%H:%M %Z}; "
                  f"{(close_at - now).total_seconds()/60:.0f} min from now{RESET}")

        if only_within is not None and not closing_within(
            now, close_at=close_at, minutes=only_within
        ):
            print(f"{DIM}not the closing run — nothing to do{RESET}")
            return 0

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

        # 1. Working orders first: a live bracket leg reserves the shares.
        for order_id in plan.cancel_first:
            try:
                await broker.cancel_order(order_id)
                print(f"  {DIM}cancelled {order_id}{RESET}")
            except BrokerError as exc:
                print(f"  {YELLOW}cancel {order_id}: {exc}{RESET}")

        # 2. Close what the BROKER says is held, at market -- but only once the
        #    cancels have landed. A cancel is accepted before it is processed,
        #    and a close sent in between is refused for insufficient quantity.
        #    That is how INTC went into the weekend of 2026-10-02 open.
        failed = await close_when_released(broker, plan.close)
        for symbol in plan.close:
            if symbol in failed:
                print(f"  {RED}close {symbol}: {failed[symbol]}{RESET}")
            else:
                print(f"  {GREEN}closing {symbol}{RESET}")

        # 3. Poll. Cancels and closes are asynchronous, and a single glance can
        #    see a stale book and call it success.
        remaining, still_working = positions, orders
        for _ in range(VERIFY_SECONDS // VERIFY_INTERVAL):
            await asyncio.sleep(VERIFY_INTERVAL)
            remaining = await broker._get("/v2/positions")
            still_working = await broker._get("/v2/orders", status="open", limit=500)
            if is_flat(positions=remaining, orders=still_working):
                break

        flat = is_flat(positions=remaining, orders=still_working)
        stuck = tuple(f"{p['symbol']}x{p.get('qty')}" for p in remaining
                      if float(p.get("qty") or 0))
        append_event(settings.audit_log_path, AuditEvent(
            event="weekend_flatten", at=datetime.now().astimezone(), symbol="",
            stage="flatten", policy=POLICY.fingerprint, build=build_id(),
            reason=(f"cancelled {len(plan.cancel_first)}, "
                    f"closed {len(plan.close) - len(failed)} of {len(plan.close)}, "
                    f"flat={flat}"
                    + (f", STILL OPEN: {', '.join(stuck)}" if stuck else "")),
        ))

        if not flat:
            print(f"\n{RED}{BOLD}NOT FLAT INTO THE WEEKEND{RESET}")
            if stuck:
                print(f"{RED}open positions: {', '.join(stuck)}{RESET}")
            if not is_flat(positions=[], orders=still_working):
                print(f"{RED}working orders still live{RESET}")
            print(f"{RED}Close these by hand before the bell.{RESET}")
            return 1

        print(f"\n{GREEN}{BOLD}flat — no positions, no working orders{RESET}")
        return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--submit", action="store_true",
                   help="actually cancel and close; omit for a dry run")
    p.add_argument("--only-if-closing-within", type=int, default=None,
                   metavar="MINUTES",
                   help="act only when the bell is this close; lets several "
                        "triggers share the job across normal and early closes")
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.submit, a.only_if_closing_within)))
