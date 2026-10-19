"""Builds Gemma's short memory block (advisor/gemma_memory.py) from the audit
log, her latest review and daily closes. Never raises: no memory is a block
that says so, never a failed trading run."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx

from scanner.advisor import gemma_memory as gm
from scanner.config import settings
from scanner.execution.scorecard import read_rows
from scanner.features.bars import fetch_many

REVIEWS = Path("data/gemma/reviews")


def latest_review(reviews_dir: Path = REVIEWS) -> dict | None:
    try:
        return json.loads((reviews_dir / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def memory_block(client: httpx.AsyncClient, *, held, today: date | None = None,
                       reviews_dir: Path = REVIEWS) -> str:
    if not gm.ENABLED:
        return ""
    try:
        rows = read_rows(settings.audit_log_path)
        review = latest_review(reviews_dir)
        wanted = tuple(sorted(gm.symbols_to_price(rows, review=review)))
        prices: dict[str, dict[str, float]] = {}
        if wanted:
            bars, _ = await fetch_many(wanted, client, range_="3mo")
            for s, b in bars.items():
                prices[s] = {**{d.isoformat(): c for d, c in zip(b.dates, b.closes)},
                             "now": b.closes[-1]}
        return gm.block(rows, review=review, change_since=gm.change_lookup(prices),
                        held=held, today=today or date.today())
    except Exception as exc:                              # noqa: BLE001
        return f"YOUR SHORT MEMORY -- unavailable today ({type(exc).__name__})."
