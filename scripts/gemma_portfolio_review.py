"""Gemma's morning portfolio review — read-only, advisory, never enforced.

    python scripts/gemma_portfolio_review.py            # write the review file
    python scripts/gemma_portfolio_review.py --show     # print to stdout too

Reads the audit log and broker state, writes `data/brief/portfolio-review.json`.
Gemma reads it at 05:45 AK as part of her facts block — advisory only, nothing
in it blocks, sizes, or closes a trade.

Safe to run on a schedule during a frozen evaluation window: it places no
order, cancels nothing, and touches no policy.
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
from scanner.execution.portfolio_review import build_review, review_text
from scanner.execution.provenance import build_id
from scanner.execution.scorecard import read_rows
from scanner.models import AuditEvent

BRIEF_DIR = Path("data/brief")
REVIEW_FILE = BRIEF_DIR / "portfolio-review.json"


def _owner_map_from_orders(orders: list[dict]) -> dict[str, str]:
    """Rebuild symbol -> strategy from order history (newest filled buy wins)."""
    from scanner.execution.ownership import owners

    return owners(orders)


async def main(show: bool) -> int:
    rows = read_rows(settings.audit_log_path)

    async with httpx.AsyncClient(timeout=60) as client:
        broker = Alpaca(
            settings.alpaca_key_id, settings.alpaca_secret_key, client
        )
        try:
            positions = await broker._get("/v2/positions")
        except BrokerError as exc:
            print(f"  ! positions unreadable: {exc}")
            positions = []
        try:
            orders = await broker._get("/v2/orders", status="open", nested="true")
        except BrokerError as exc:
            print(f"  ! orders unreadable: {exc}")
            orders = []
        try:
            account = await broker.account()
        except BrokerError as exc:
            print(f"  ! account unreadable: {exc}")
            account = {"equity": 0.0, "buying_power": 0.0, "status": "UNKNOWN"}

    owner_map = _owner_map_from_orders(
        await _all_orders(settings.alpaca_key_id, settings.alpaca_secret_key)
    )

    # Sector map from the universe.
    from scanner.sources import composite

    try:
        universe = await composite.load_universe(client)
        sector_of = {m.symbol: m.sector for m in universe}
    except Exception:
        sector_of = {}

    review = build_review(
        rows=rows,
        positions=positions,
        orders=orders,
        account=account,
        owner_map=owner_map,
        sector_of=sector_of,
    )

    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": review.date,
        "account_equity": review.account_equity,
        "account_buying_power": review.account_buying_power,
        "account_status": review.account_status,
        "positions": [p.model_dump(mode="json") for p in review.positions],
        "working_orders": [o.model_dump(mode="json") for o in review.working_orders],
        "by_book": [b.model_dump(mode="json") for b in review.by_book],
        "recent_entries": list(review.recent_entries),
        "recent_exits": list(review.recent_exits),
        "recent_refusals": [r.model_dump(mode="json") for r in review.recent_refusals],
        "sector_exposure": [s.model_dump(mode="json") for s in review.sector_exposure],
        "news_gate_refusals": [r.model_dump(mode="json") for r in review.news_gate_refusals],
        "tradingagents_ratings": [t.model_dump(mode="json") for t in review.tradingagents_ratings],
        "summary_text": review.summary_text(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    REVIEW_FILE.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    if show:
        print(review_text(review))

    print(f"  portfolio review written -> {REVIEW_FILE}")
    append_event(settings.audit_log_path, AuditEvent(
        event="portfolio_review", at=datetime.now().astimezone(), symbol="",
        stage="advisor", policy="", build=build_id(),
        reason=f"positions={len(review.positions)} "
               f"orders={len(review.working_orders)} "
               f"books={len(review.by_book)}",
    ))
    return 0


async def _all_orders(key_id: str, secret_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as client:
        broker = Alpaca(key_id, secret_key, client)
        try:
            return await broker._get("/v2/orders", status="all", limit=500, nested="true")
        except BrokerError:
            return []


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--show", action="store_true",
                   help="print the review to stdout as well as writing the file")
    a = p.parse_args()
    sys.exit(asyncio.run(main(a.show)))
