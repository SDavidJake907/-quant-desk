"""Fetches what advisor/sold_review.py needs: the broker's fills and orders since DAYS ago,
the audit log's exit reasons and today's prices. Never raises: no block is a missing
block, never a failed trading run."""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx

from scanner.advisor import sold_review as sr
from scanner.config import settings
from scanner.execution.scorecard import read_rows
from scanner.features.bars import fetch_many


async def _fills(broker, since: datetime) -> list[dict]:
    out, token = [], None
    while True:
        params = {"after": since.isoformat(), "direction": "desc", "page_size": 100}
        if token:
            params["page_token"] = token
        page = await broker._get("/v2/account/activities/FILL", **params)
        out += page
        if len(page) < 100:
            return out
        token = page[-1]["id"]


async def _orders(broker, since: datetime) -> dict[str, dict]:
    out: dict[str, dict] = {}
    until = None
    while True:
        params = {"status": "all", "after": since.isoformat(), "limit": 500, "direction": "desc",
                  "nested": "true"}
        if until:
            params["until"] = until
        page = await broker._get("/v2/orders", **params)
        for o in page:
            for x in (o, *(o.get("legs") or ())):
                out[str(x["id"])] = x
        if len(page) < 500:
            return out
        until = page[-1]["submitted_at"]


def _exits() -> list[tuple[str, datetime, str]]:
    out = []
    for r in read_rows(settings.audit_log_path):
        if str(r.get("event", "")).endswith("_exit") and r.get("symbol"):
            try:
                out.append((str(r["symbol"]), datetime.fromisoformat(str(r["at"])), str(r.get("reason", ""))))
            except ValueError:
                continue
    return out


async def sold_block(broker, client: httpx.AsyncClient, *, days: int = sr.DAYS) -> str:
    try:
        since = datetime.now().astimezone() - timedelta(days=days)
        trips = sr.round_trips(await _fills(broker, since), await _orders(broker, since))
        trips = sr.with_reasons(trips, _exits())
        bars, _ = await fetch_many(tuple(sorted({t.symbol for t in trips})), client, range_="5d")
        return sr.block(trips, {s: b.closes[-1] for s, b in bars.items() if b.closes}, days=days)
    except Exception as exc:                              # noqa: BLE001
        print(f"  sold-positions block unavailable ({type(exc).__name__}: {exc})")
        return ""
