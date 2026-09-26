"""Median relative strength per sector, computed once a day.

WHY THIS IS NOT COMPUTED IN THE SCAN
It was. `trade.py:_sector_standing()` fetched two years of DAILY bars for all
501 universe symbols plus `^GSPC`, derived relative strength per symbol, and
took a median per sector — on every scan that found a candidate. Measured:

    cold  14.42s      (first scan of the day, cache empty)
    warm   4.45s      (daily bar cache warm)

against a full 501-symbol scan of about 17s. So roughly a quarter again on
every scan, and the worst of it on the FIRST scan of the day, which is the one
running closest to the open.

It cannot change within a trading day. The inputs are daily bars; the median
sector strength at 06:03 is the median at 11:33. Recomputing it thirteen times
a day produced thirteen identical answers.

DATE-STAMPED, NOT TTL
The same reasoning `features/bars.py` uses for daily bars: these change once a
day, so a date stamp is correct and a TTL is not. A stamp from a previous
session is refused rather than served, which is the fault that put the bar
cache in FREEZE.md — it renewed its own lease and served stale data as fresh.

ADVISORY ONLY
Nothing here gates a trade. The medians are context for the ranking prompt, so
a missing file degrades the prompt and must never stop a scan. Every reader
gets `{}` on any failure.
"""

from __future__ import annotations

import json
import statistics
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from scanner.features import technicals
from scanner.features.bars import fetch_bars, fetch_many

#: Beside the other derived stores, not in `data/bars`, which is keyed cache.
PATH = Path("data/sector_rs.json")

BENCHMARK = "^GSPC"
RANGE = "2y"


def load(*, today: date | None = None, path: Path | None = None
         ) -> dict[str, float]:
    """Today's medians, or `{}` if absent, unreadable, or from another day."""
    target = path or PATH
    stamp = (today or date.today()).isoformat()
    try:
        payload: Any = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("date") != stamp:
        return {}
    medians = payload.get("medians")
    if not isinstance(medians, dict):
        return {}
    return {str(k): float(v) for k, v in medians.items()
            if isinstance(v, (int, float))}


def save(medians: dict[str, float], *, today: date | None = None,
         path: Path | None = None) -> Path:
    """Write the medians under today's date. Atomic, like the bar store."""
    target = path or PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    body = {"date": (today or date.today()).isoformat(), "medians": medians}
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(body), encoding="utf-8")
    temporary.replace(target)
    return target


async def compute(client: httpx.AsyncClient, universe) -> dict[str, float]:
    """Median relative strength per sector — leaders positive, laggards not.

    Arithmetic, not judgement: the model's job is to read the news against it.
    """
    try:
        benchmark = technicals.compute(
            await fetch_bars(BENCHMARK, client, range_=RANGE))
        daily, _ = await fetch_many(
            tuple(m.symbol for m in universe), client, range_=RANGE)
    except Exception:                                        # noqa: BLE001
        return {}
    buckets: dict[str, list[float]] = {}
    for member in universe:
        series = daily.get(member.symbol)
        if series is None:
            continue
        strength = technicals.relative_strength(
            technicals.compute(series), benchmark)
        if strength is not None:
            buckets.setdefault(member.sector, []).append(strength)
    return {s: statistics.median(v) for s, v in buckets.items() if v}


async def refresh(client: httpx.AsyncClient, universe) -> dict[str, float]:
    """Compute and persist. For the daily collector."""
    medians = await compute(client, universe)
    if medians:
        save(medians)
    return medians


async def today(client: httpx.AsyncClient, universe) -> dict[str, float]:
    """Cached medians if the daily job has run, otherwise compute and store.

    The fallback matters: a scan must not depend on another task having
    succeeded. It pays the old cost once and every later scan that day is free.
    """
    cached = load()
    if cached:
        return cached
    return await refresh(client, universe)
