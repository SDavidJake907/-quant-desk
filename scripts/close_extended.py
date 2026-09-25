"""Close positions while the exchange is open but stops are not.

    python scripts/close_extended.py                      # dry run
    python scripts/close_extended.py --submit             # act
    python scripts/close_extended.py --submit --symbol PSX
    python scripts/close_extended.py --submit --slippage-bps 100

WHY THIS EXISTS
From 20:00 to 04:00 ET the market trades and this system has no way out. The
bracket's stop leg is a stop order and stop orders are regular-hours only;
`flatten.py` closes with `DELETE /v2/positions`, which is a market order and
also regular-hours only. About half the positions this strategy holds are held
overnight by design, so for those hours there has been no exit at all — not
automatic, and not by hand either.

THIS IS BEST EFFORT AND SAYS SO
A limit order is not a stop. It does not trigger on a level, and in a fast move
it is precisely the order that does not fill. This script polls until the book
is actually flat and **exits non-zero if it is not**, because the failure worth
guarding against is reporting a submitted order as a completed exit.

IT ONLY CLOSES
Every order side comes from the sign of a position the broker reports. A flat
book sends nothing, and running it twice cannot open a short.

NOT WIRED TO A SCHEDULE
Deliberately manual. An unattended job that sells into an overnight book
deserves more evidence than this has, and `WORKLIST.md` §9 is where that
evidence gets collected.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

import httpx

from scanner.config import settings
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.audit import append_event
from scanner.execution.extended_exit import (
    DEFAULT_SLIPPAGE_BPS,
    exit_payload,
    plan_extended_exit,
)
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id
from scanner.models import AuditEvent

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

POLICY = TradingPolicy()

#: Seconds to keep asking whether the book is empty. Longer than the regular
#: flatten's budget: an overnight limit needs time to find a counterparty.
VERIFY_SECONDS = 90
VERIFY_INTERVAL = 5


def _still_held(positions) -> tuple[str, ...]:
    return tuple(
        f"{p['symbol']}x{p.get('qty')}" for p in positions or ()
        if float(p.get("qty") or 0)
    )


async def main(
    submit: bool, symbols: tuple[str, ...], slippage_bps: float,
    allow_open_market: bool,
) -> int:
    only = frozenset(s.upper() for s in symbols) if symbols else None

    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        try:
            account = await broker.account()
            is_open = await broker.market_open()
        except BrokerError as exc:
            print(f"{RED}broker unreachable: {exc}{RESET}")
            return 2

        if is_open and not allow_open_market:
            print(f"{YELLOW}regular hours are open — use scripts/flatten.py, "
                  f"which closes at market and does not need a limit to fill."
                  f"{RESET}")
            print(f"{DIM}pass --allow-open-market to override{RESET}")
            return 0

        print(f"{DIM}paper account: equity {account.equity:,.2f}, "
              f"status {account.status}{RESET}")
        print(f"{DIM}regular session open: {is_open}; "
              f"limit {slippage_bps:.0f}bp through the mark{RESET}")

        try:
            positions = await broker._get("/v2/positions")
            orders = await broker._get("/v2/orders", status="open", limit=500)
        except BrokerError as exc:
            print(f"{RED}could not read the book: {exc}{RESET}")
            return 2

        plan = plan_extended_exit(
            positions, orders, slippage_bps=slippage_bps, only=only,
        )
        print(f"{BOLD}{plan.describe()}{RESET}")
        for leg in plan.legs:
            print(f"  {leg.describe()}")
        for note in plan.skipped:
            print(f"  {YELLOW}skipped {note}{RESET}")

        if plan.is_empty:
            return 0
        if not submit:
            print(f"{YELLOW}dry run — nothing sent. Pass --submit to act.{RESET}")
            return 0

        # 1. Cancel first: a resting bracket leg reserves the shares, and an
        #    exit submitted underneath one is rejected for insufficient qty.
        for order_id in plan.cancel_first:
            try:
                await broker.cancel_order(order_id)
                print(f"  {DIM}cancelled {order_id}{RESET}")
            except BrokerError as exc:
                print(f"  {YELLOW}cancel {order_id}: {exc}{RESET}")

        # 2. One limit order per position, priced through the mark.
        sent = 0
        for leg in plan.legs:
            try:
                await broker.submit(exit_payload(leg))
                print(f"  {GREEN}sent {leg.describe()}{RESET}")
                sent += 1
            except BrokerError as exc:
                print(f"  {RED}{leg.symbol}: {exc}{RESET}")

        # 3. Poll. A submitted limit is not a filled limit, and overnight it
        #    may never become one.
        remaining = positions
        for _ in range(VERIFY_SECONDS // VERIFY_INTERVAL):
            await asyncio.sleep(VERIFY_INTERVAL)
            try:
                remaining = await broker._get("/v2/positions")
            except BrokerError:
                continue
            if not _still_held(remaining):
                break

        stuck = _still_held(remaining)
        append_event(settings.audit_log_path, AuditEvent(
            event="extended_exit", at=datetime.now().astimezone(), symbol="",
            stage="exit", policy=POLICY.fingerprint, build=build_id(),
            reason=(f"cancelled {len(plan.cancel_first)}, sent {sent}, "
                    f"flat={not stuck}"
                    + (f", STILL OPEN: {', '.join(stuck)}" if stuck else "")),
        ))

        if stuck:
            print(f"\n{RED}{BOLD}NOT FLAT — limit orders did not fill{RESET}")
            print(f"{RED}still held: {', '.join(stuck)}{RESET}")
            print(f"{DIM}Overnight books are thin. Re-run with a wider "
                  f"--slippage-bps, or wait for regular hours and use "
                  f"scripts/flatten.py.{RESET}")
            return 1

        print(f"\n{GREEN}{BOLD}flat — every position closed{RESET}")
        return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--submit", action="store_true",
                   help="actually cancel and close; omit for a dry run")
    p.add_argument("--symbol", nargs="*", default=[], metavar="SYM",
                   help="close only these; default is every position")
    p.add_argument("--slippage-bps", type=float, default=DEFAULT_SLIPPAGE_BPS,
                   help=f"how far through the mark to price the limit "
                        f"(default {DEFAULT_SLIPPAGE_BPS:.0f})")
    p.add_argument("--allow-open-market", action="store_true",
                   help="run even during regular hours; flatten.py is better")
    a = p.parse_args()
    raise SystemExit(asyncio.run(
        main(a.submit, tuple(a.symbol), a.slippage_bps, a.allow_open_market)))
