"""Analyst estimates and a few fundamentals per stock, from Yahoo's quoteSummary.

Operator 2026-10-19: add the data Gemma has never seen -- what the companies
earn and whether analysts are raising or cutting their forecasts (she asked for
estimate revisions on 10-17). Modules: earningsTrend (EPS estimate now vs 7/30/
60/90 days ago, and how many analysts raised/cut it), financialData (growth,
margins), summaryDetail (forward P/E).

Yahoo needs a session cookie plus a "crumb" token; both come from two plain
GETs. Cached per symbol per day under data/fundamentals/YYYY-MM-DD/. Any
failure returns None for that symbol: no line, never a blocked run.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from scanner.config import settings
from scanner.models import Frozen

COOKIE_URL = "https://fc.yahoo.com"
CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
MODULES = "earningsTrend,financialData,summaryDetail"
CACHE = Path("data/fundamentals")
CONCURRENCY = 4


class Fundamentals(Frozen):
    symbol: str
    period: str = ""                       # the estimate used: "0y" (this fiscal year)
    eps_now: float | None = None
    eps_30d_ago: float | None = None
    eps_90d_ago: float | None = None
    up_30d: int | None = None
    down_30d: int | None = None
    forward_pe: float | None = None
    revenue_growth: float | None = None    # fraction, year over year (latest quarter)
    earnings_growth: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None


def _raw(node: Any) -> float | None:
    if isinstance(node, Mapping):
        node = node.get("raw")
    return float(node) if isinstance(node, (int, float)) else None


def parse(symbol: str, result: Mapping[str, Any]) -> Fundamentals:
    """One quoteSummary result -> Fundamentals. This fiscal year's estimate (0y)
    is used: the quarter flips every few weeks, the year is steadier."""
    trends = {t.get("period"): t for t in (result.get("earningsTrend") or {}).get("trend") or []}
    t = trends.get("0y") or trends.get("+1y") or {}
    eps, rev = t.get("epsTrend") or {}, t.get("epsRevisions") or {}
    fd, sd = result.get("financialData") or {}, result.get("summaryDetail") or {}

    def count(key: str) -> int | None:
        v = _raw(rev.get(key))
        return int(v) if v is not None else None

    return Fundamentals(
        symbol=symbol, period=str(t.get("period") or ""),
        eps_now=_raw(eps.get("current")), eps_30d_ago=_raw(eps.get("30daysAgo")),
        eps_90d_ago=_raw(eps.get("90daysAgo")),
        up_30d=count("upLast30days"), down_30d=count("downLast30days"),
        forward_pe=_raw(sd.get("forwardPE")),
        revenue_growth=_raw(fd.get("revenueGrowth")), earnings_growth=_raw(fd.get("earningsGrowth")),
        gross_margin=_raw(fd.get("grossMargins")), operating_margin=_raw(fd.get("operatingMargins")))


async def _crumb(client: httpx.AsyncClient) -> str:
    await client.get(COOKIE_URL)            # sets the session cookie (any status)
    r = await client.get(CRUMB_URL)
    r.raise_for_status()
    crumb = r.text.strip()
    if not crumb or "<" in crumb:
        raise ValueError("no crumb")
    return crumb


async def fetch_many(symbols: Iterable[str], *, today: date | None = None,
                     cache_dir: Path = CACHE) -> dict[str, Fundamentals]:
    """{symbol: Fundamentals} for every symbol Yahoo answered; the rest are absent."""
    day_dir = cache_dir / (today or date.today()).isoformat()
    out: dict[str, Fundamentals] = {}
    todo = []
    for s in dict.fromkeys(symbols):
        path = day_dir / f"{s}.json"
        if path.exists():
            try:
                out[s] = parse(s, json.loads(path.read_text(encoding="utf-8")))
                continue
            except (OSError, ValueError):
                pass
        todo.append(s)
    if not todo:
        return out
    try:
        async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent},
                                     follow_redirects=True, timeout=20.0) as client:
            crumb = await _crumb(client)
            sem = asyncio.Semaphore(CONCURRENCY)

            async def one(s: str) -> None:
                async with sem:
                    try:
                        r = await client.get(SUMMARY_URL.format(symbol=s),
                                             params={"modules": MODULES, "crumb": crumb})
                        r.raise_for_status()
                        result = r.json()["quoteSummary"]["result"][0]
                    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                        return
                    out[s] = parse(s, result)
                    day_dir.mkdir(parents=True, exist_ok=True)
                    (day_dir / f"{s}.json").write_text(json.dumps(result), encoding="utf-8")

            await asyncio.gather(*(one(s) for s in todo))
    except (httpx.HTTPError, ValueError) as exc:
        print(f"  fundamentals unavailable ({type(exc).__name__}: {exc}) -- no analyst lines today")
    return out
