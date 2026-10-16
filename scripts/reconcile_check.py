"""Reconciliation verification: audit log intents vs broker state.

    python scripts/reconcile_check.py            # report mismatches, exit non-zero
    python scripts/reconcile_check.py --write    # also write mismatch events to audit log

Reads the audit log for trade_intent/order_submitted records, queries the broker
for all orders and positions, and reports:

  * intents with no matching broker order (sent but lost — order may never have
    reached Alpaca, or the client_order_id was dropped)
  * broker orders with no matching audit intent (untracked — opened outside
    this system, or a logging failure)
  * fill prices that differ from intended prices by more than a threshold
    (slippage beyond the 0.30% entry cushion)

Read-only by default. With --write, mismatch events are appended to the audit
log as `reconcile_mismatch` records. Safe to run on a schedule during a frozen
evaluation window — it places no order and changes no position.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

from scanner.config import settings
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.audit import append_event
from scanner.execution.provenance import build_id
from scanner.execution.reconcile import classify, reconcile
from scanner.models import AuditEvent, TradeIntent

#: Slippage threshold as a fraction of intended entry. Beyond this, the fill
#: price is far enough from intended that the R-multiple changes materially.
SLIPPAGE_THRESHOLD = 0.005  # 0.5%


def _load_intents(path: str) -> dict[str, dict]:
    """Read trade_intent and order_submitted records from the audit log."""
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
        coid = str(intent.get("client_order_id") or row.get("order_id") or "")
        if coid:
            intents[coid] = intent
    return intents


def _coid_of(order: dict) -> str:
    return str(order.get("client_order_id") or "")


async def main(write: bool) -> int:
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
    trades = reconcile(intents, orders)

    intent_coids = {t.client_order_id for t in trades if t.client_order_id}
    order_coids = {_coid_of(o) for o in orders}

    # Intents with no broker order.
    lost = [t for t in trades if t.client_order_id
            and t.client_order_id not in order_coids]
    for t in lost:
        msg = (f"lost intent {t.client_order_id}: {t.symbol} "
               f"{t.shares} sh @ {t.intended_entry:.2f}, stop {t.intended_stop:.2f}, "
               f"target {t.intended_target:.2f} — no matching broker order")
        problems.append(msg)
        print(f"  {msg}")
        if write:
            append_event(settings.audit_log_path, AuditEvent(
                event="reconcile_mismatch", at=datetime.now().astimezone(),
                symbol=t.symbol, reason="lost_intent_no_broker_order",
                stage="reconcile", policy="", build=build_id(),
            ))

    # Broker orders with no audit intent (untracked).
    untracked_orders = [o for o in orders if _coid_of(o)
                        and _coid_of(o) not in intent_coids
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
                event="reconcile_mismatch", at=datetime.now().astimezone(),
                symbol=str(o.get("symbol") or ""), reason="untracked_broker_order",
                stage="reconcile", policy="", build=build_id(),
            ))

    # Fill price slippage beyond threshold.
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
                    event="reconcile_mismatch", at=datetime.now().astimezone(),
                    symbol=t.symbol, reason=f"slippage {slip * 100:.2f}%",
                    stage="reconcile", policy="", build=build_id(),
                ))

    # Positions with no intent at all (untracked positions).
    position_symbols = {str(p.get("symbol") or "") for p in positions}
    intent_symbols = {str(i.get("symbol") or "") for i in intents.values()}
    untracked_positions = position_symbols - intent_symbols
    for sym in sorted(untracked_positions):
        msg = f"untracked position {sym}: held by broker, no audit intent"
        problems.append(msg)
        print(f"  {msg}")
        if write:
            append_event(settings.audit_log_path, AuditEvent(
                event="reconcile_mismatch", at=datetime.now().astimezone(),
                symbol=sym, reason="untracked_position_no_intent",
                stage="reconcile", policy="", build=build_id(),
            ))

    if problems:
        print(f"\nRECONCILIATION: {len(problems)} mismatch(es) found")
        return 1

    print("\nRECONCILIATION: audit log matches broker state")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--write", action="store_true",
                   help="write mismatch events to the audit log")
    a = p.parse_args()
    sys.exit(asyncio.run(main(a.write)))
