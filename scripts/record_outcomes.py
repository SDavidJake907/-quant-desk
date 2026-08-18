"""Append what actually happened to each closed trade.

    python scripts/record_outcomes.py            # show what would be written
    python scripts/record_outcomes.py --write    # append the records

READ-MOSTLY, AND OUTSIDE THE FROZEN TRADING PATH. It reads the broker and
appends to the audit log. It places no order, cancels nothing, and touches no
policy, so it can run during a frozen evaluation window without ending it.

WHY IT EXISTS
The audit log records what was decided and never what came of it. Realized R is
recomputed on demand from Alpaca's order history, which is not permanent —
when the broker prunes it, the only record of how a trade ended goes too.

Eight dimensions are logged on every decision and enforced on none, each so it
could be settled later on data nobody chose it from: RSI gate, session slot,
weekday, LLM ranking, equal-lows pool, liquidity tier, priority rank, and the
uncapped-exit candidate. None of them can be answered by a log that knows only
what was intended, however many trades accumulate.

APPEND ONLY
A `trade_outcome` record is NEW and linked by `client_order_id`. Nothing
earlier is rewritten. An outcome written by editing the original intent would
be indistinguishable from an intent that had always said so — and a log that
can be revised after the fact settles nothing.

SAFE TO RUN REPEATEDLY
Outcomes already in the log are skipped. The job is meant to run daily and
again after each Friday flatten, so a duplicate write is the normal case to
defend against, not an edge case.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

from scanner.config import settings
from scanner.execution.alpaca import Alpaca
from scanner.execution.audit import append_event
from scanner.execution.outcomes import EVENT, outcome_from
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id
from scanner.execution.reconcile import reconcile
from scanner.models import AuditEvent

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

POLICY = TradingPolicy()
_HERE = Path(__file__).resolve().parent


def _load_intents():
    """`scripts/positions.py` owns the audit reader and is not a package."""
    spec = importlib.util.spec_from_file_location("_positions", _HERE / "positions.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_positions"] = module
    spec.loader.exec_module(module)
    return module.load_intents(settings.audit_log_path)


def _origin(path: str) -> dict[str, tuple[str, str]]:
    """The (policy, build) each trade was DECIDED under, by client_order_id.

    An outcome record must be attributed to the rules and the code that placed
    the trade, not to whatever is checked out when the outcome is written. The
    evaluation window groups by (policy, build); stamping the recorder's values
    would file every historical trade under today's policy and silently inflate
    the current sample.
    """
    target = Path(path)
    if not target.exists():
        return {}
    origin: dict[str, tuple[str, str]] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        coid = (record.get("intent") or {}).get("client_order_id")
        if coid and coid not in origin:
            origin[coid] = (record.get("policy") or "", record.get("build") or "")
    return origin


def _already_recorded(path: str) -> set[str]:
    """client_order_ids that already carry an outcome."""
    target = Path(path)
    if not target.exists():
        return set()
    seen: set[str] = set()
    for line in target.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue          # a torn line must not stop the rest
        if record.get("event") == EVENT:
            coid = (record.get("outcome") or {}).get("client_order_id")
            if coid:
                seen.add(coid)
    return seen


async def main(write: bool) -> int:
    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        # nested=true associates bracket legs with their parent; without it the
        # exit fills are invisible and every trade reads as still open.
        orders = await broker._get("/v2/orders", status="all", nested="true",
                                   limit=500, direction="desc")

    trades = reconcile(_load_intents(), orders)
    seen = _already_recorded(settings.audit_log_path)

    pending, skipped = [], 0
    for trade in trades:
        outcome = outcome_from(trade)
        if outcome is None:
            continue
        if outcome.client_order_id in seen:
            skipped += 1
            continue
        pending.append(outcome)

    print(f"{DIM}{len(trades)} reconciled, {skipped} already recorded{RESET}")
    if not pending:
        print(f"{GREEN}nothing new to record{RESET}")
        return 0

    print(f"\n{BOLD}{len(pending)} outcome(s) to append{RESET}")
    print(f"  {'symbol':<8}{'reason':<11}{'entry':>9}{'exit':>9}"
          f"{'R':>8}{'dollars':>10}")
    for outcome in pending:
        tint = GREEN if outcome.realized_r > 0 else RED
        print(f"  {outcome.symbol:<8}{outcome.exit_reason:<11}"
              f"{outcome.entry_fill:>9.2f}{outcome.exit_fill:>9.2f}"
              f"{tint}{outcome.realized_r:>+8.3f}{RESET}"
              f"{outcome.realized_dollars:>+10.2f}")

    if not write:
        print(f"\n{YELLOW}dry run — nothing written. Pass --write to append.{RESET}")
        return 0

    origin = _origin(settings.audit_log_path)
    for outcome in pending:
        # The policy and build the TRADE ran under, not the recorder's. An
        # outcome filed under today's fingerprint would inflate the current
        # window with trades decided by older rules.
        policy, build = origin.get(outcome.client_order_id,
                                   (POLICY.fingerprint, build_id()))
        append_event(settings.audit_log_path, AuditEvent(
            event=EVENT, at=datetime.now().astimezone(), symbol=outcome.symbol,
            stage="outcome", policy=policy, build=build, outcome=outcome,
        ))
    print(f"\n{GREEN}appended {len(pending)} outcome record(s){RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--write", action="store_true",
                   help="append the records; omit for a dry run")
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.write)))
