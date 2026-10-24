"""Builds Gemma's short memory block (advisor/gemma_memory.py) from the audit
log, her latest review and daily closes. Never raises: no memory is a block
that says so, never a failed trading run."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx

from scanner.advisor import forecast_grade as fg
from scanner.advisor import outlook
from scanner.advisor import gemma_memory as gm
from scanner.config import settings
from scanner.execution.scorecard import read_rows
from scanner.features.bars import fetch_bars, fetch_many

REVIEWS = Path("data/gemma/reviews")


def latest_review(reviews_dir: Path = REVIEWS) -> dict | None:
    try:
        return json.loads((reviews_dir / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def forecast_score(client: httpx.AsyncClient, rows=None) -> dict:
    """Her graded 5-day forecasts so far (advisor/forecast_grade.py). Never raises."""
    try:
        rows = rows if rows is not None else read_rows(settings.audit_log_path)
        made = fg.extract(rows)
        if not made:
            return fg.score([])
        bars, _ = await fetch_many(tuple(sorted({f.symbol for f in made})), client, range_="3mo")
        spx = await fetch_bars("^GSPC", client, range_="3mo")
        return fg.score(fg.grade(made, bars, spx))
    except Exception as exc:                              # noqa: BLE001
        return {"n": 0, "error": type(exc).__name__}


async def outlook_score(client: httpx.AsyncClient, made: list[outlook.Outlook]) -> dict:
    """Her graded 10-day outlooks so far (advisor/outlook.py). Never raises."""
    try:
        if not made:
            return outlook.score([])
        bars, _ = await fetch_many(tuple(sorted({o.symbol for o in made})), client, range_="6mo")
        return outlook.score(outlook.grade(made, bars))
    except Exception as exc:                              # noqa: BLE001
        return {"n": 0, "error": type(exc).__name__}


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
        score = await forecast_score(client, rows)
        return gm.block(rows, review=review, change_since=gm.change_lookup(prices),
                        held=held, today=today or date.today(), extras=(fg.text(score),))
    except Exception as exc:                              # noqa: BLE001
        return f"YOUR SHORT MEMORY -- unavailable today ({type(exc).__name__})."
