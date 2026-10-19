"""Daily reconciliation: audit log intents vs broker state.

Runs at end of day (after outcomes have settled) to verify that every trade
intent in the audit log has a corresponding broker order, and every broker
order has a corresponding audit intent. Reports mismatches:

  * Lost intents: audit log has a trade_intent but broker has no order
    (order may never have reached Alpaca, or client_order_id was dropped)
  * Untracked orders: broker has an order but audit log has no intent
    (order placed outside the system, or logging failure)
  * Fill mismatches: order filled at a price significantly different from
    intended entry (slippage beyond threshold)
  * Untracked positions: broker holds a position but no audit intent exists

Read-only. Places no orders. Touches no policy. Safe to run during a frozen
evaluation window.

Append mismatch events to the audit log as reconcile_mismatch events for
permanent record.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from scanner.config import settings
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.audit import append_event
from scanner.execution.provenance import build_id
from scanner.execution.reconcile import reconcile as reconcile_trades
from scanner.models import AuditEvent

#: Slippage threshold as a fraction of intended entry. Beyond this, the fill
#: price differs materially from intended entry.
SLIPPAGE_THRESHOLD = 0.005  # 0.5%

#: Reconciliation event name written to the audit log.
RECONCILE_EVENT = "reconcile_mismatch"


def _load_intents(path: str) -> dict[str, dict]:
    """Read trade_intent and order_submitted records from the audit log.

    Dry-run intents (previews that were never meant to reach the broker) are
    skipped: counting them reported every preview as a "lost intent".
    """
    intents: dict[str, dict] = {}
    log = Path(path)
    if not log.exists():
        return intents
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        event = row.get("event", "")
        if event not in ("trade_intent", "order_submitted"):
            continue
        intent = row.get("intent") or {}
        if intent.get("dry_run"):
            continue
        coid =str(intent.get("client_order_id") or row.get("order_id") or "")
        if coid:
            intents.setdefault(coid, intent)
    return intents


def _coid_of(order: dict) -> str:
    return str(order.get("client_order_id") or "")


async def main(write: bool = False) -> int:
    """Run reconciliation between audit log and broker state.

    Returns non-zero exit code if mismatches found.
    """
    problems: list[str] = []

    intents = _load_intents(settings.audit_log_path)

    async with httpx.AsyncClient(timeout=60) as client:
        broker = Alpaca(
            settings.alpaca_key_id, settings.alpaca_secret_key, client
        )
        try:
            orders = await broker._get("/v2/orders", status="all", limit=500,
                                       nested="true")
        except BrokerError as exc:
            print(f"cannot read broker orders: {exc}")
            return 1
        positions = await broker._get("/v2/positions")

    # Reconcile intents to broker orders.
    trades = reconcile_trades(intents, orders)

    order_coids = {_coid_of(o) for o in orders}

    # --- Lost intents: audit log has intent but broker has no order ---
    lost = [t for t in trades if t.client_order_id
            and t.client_order_id not in order_coids]
    for t in lost:
        msg = (f"lost intent {t.client_order_id}: {t.symbol} "
               f"{t.shares} sh @ {t.intended_entry:.2f}, "
               f"stop {t.intended_stop:.2f}, target {t.intended_target:.2f} "
               f"-- no matching broker order")
        problems.append(msg)
        print(f"  {msg}")
        if write:
            append_event(settings.audit_log_path, AuditEvent(
                event=RECONCILE_EVENT, at=datetime.now(timezone.utc),
                symbol=t.symbol, reason="lost_intent_no_broker_order",
                stage="reconcile", policy="", build=build_id(),
            ))

    # --- Untracked orders: broker has order but audit log has no intent ---
    intent_coids_set = {str(i.get("client_order_id") or "")
                        for i in intents.values() if i.get("client_order_id")}
    untracked_orders = [o for o in orders if _coid_of(o)
                        and _coid_of(o) not in intent_coids_set
                        and not str(o.get("client_order_id", "")).startswith(
                            ("smc-", "pck-", "gem-", "man-", "mom-"))]
    for o in untracked_orders:
        coid = _coid_of(o)
        msg = (f"untracked broker order {coid}: {o.get('symbol')} "
               f"{o.get('side')} {o.get('type')} status={o.get('status')}")
        problems.append(msg)
        print(f"  {msg}")
        if write:
            append_event(settings.audit_log_path, AuditEvent(
                event=RECONCILE_EVENT, at=datetime.now(timezone.utc),
                symbol=str(o.get("symbol") or ""),
                reason="untracked_broker_order",
                stage="reconcile", policy="", build=build_id(),
            ))

    # --- Fill slippage beyond threshold ---
    for t in trades:
        if t.entry_fill is None or t.intended_entry <= 0:
            continue
        slip = abs(t.entry_fill - t.intended_entry) / t.intended_entry
        if slip > SLIPPAGE_THRESHOLD:
            msg = (f"slippage {t.symbol}: intended {t.intended_entry:.2f}, "
                   f"filled {t.entry_fill:.2f} ({slip * 100:.2f}% off)")
            problems.append(msg)
            print(f"  {msg}")
            if write:
                append_event(settings.audit_log_path, AuditEvent(
                    event=RECONCILE_EVENT, at=datetime.now(timezone.utc),
                    symbol=t.symbol, reason=f"slippage {slip * 100:.2f}%",
                    stage="reconcile", policy="", build=build_id(),
                ))

    # --- Untracked positions: broker holds position but no audit intent ---
    position_symbols = {str(p.get("symbol") or "") for p in positions}
    intent_symbols = {str(i.get("symbol") or "") for i in intents.values()}
    untracked_positions = position_symbols - intent_symbols
    for sym in sorted(untracked_positions):
        msg = f"untracked position {sym}: held by broker, no audit intent"
        problems.append(msg)
        print(f"  {msg}")
        if write:
            append_event(settings.audit_log_path, AuditEvent(
                event=RECONCILE_EVENT, at=datetime.now(timezone.utc),
                symbol=sym, reason="untracked_position_no_intent",
                stage="reconcile", policy="", build=build_id(),
            ))

    if problems:
        print(f"\nRECONCILIATION: {len(problems)} mismatch(es) found")
        return 1

    print("\nRECONCILIATION: audit log matches broker state")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconcile audit-log intents "
                                                 "to broker state.")
    parser.add_argument("--write", action="store_true",
                        help="write mismatch events to the audit log")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.write)))
