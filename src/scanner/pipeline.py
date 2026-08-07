"""The funnel: resolve -> fetch -> prefilter -> dedup -> triage -> report.

Ordering is the whole design. Each stage is cheaper than the one after it, so
the expensive model only ever sees what survived everything free:

    entity resolution   microseconds, deterministic, authoritative
    alias prefilter     microseconds, deterministic, unit-tested
    roundup detector    microseconds, deterministic, unit-tested
    embedding dedup     milliseconds, local
    Gemma 3 triage      ~1s per batch  <-- the only costly step

Roundups sit in front of the model rather than inside it. Asking Gemma 3 to
call a four-company listicle `example` works some of the time; counting the
company names in the title works every time, and costs nothing.

SEC filings skip triage entirely. An 8-K is self-filed and SEC-classified, so
there is nothing for a model to add or get wrong.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx

from scanner.entity import Entity, build_entity, prefilter
from scanner.llm.dedup import deduplicate
from scanner.llm.ollama import LLMError
from scanner.models import ScanReport
from scanner.roundup import NameIndex, build_index, drop_roundups
from scanner.sources import constituents, edgar, sec_tickers, yahoo_rss
from scanner.sources.constituents import ConstituentsError
from scanner.sources.edgar import EdgarError
from scanner.sources.sec_tickers import EntityMasterError
from scanner.sources.yahoo_rss import FeedError


async def resolve(ticker: str, client: httpx.AsyncClient) -> Entity | None:
    """Map a ticker to its authoritative SEC identity, or None if unlisted."""
    try:
        universe = await sec_tickers.load_universe(client)
    except EntityMasterError as exc:
        print(f"  ! entity master unavailable: {exc}")
        return None

    resolved = universe.get(ticker.upper())
    if resolved is None:
        return None
    cik, official_name = resolved
    return build_entity(ticker, cik, official_name)


async def load_name_index(client: httpx.AsyncClient) -> NameIndex:
    """Company names to count roundups against — the index we screen.

    A failure here must not take the scan down: without the index every
    article simply reaches the model, which is the old behaviour.
    """
    try:
        universe = await constituents.load(client)
    except ConstituentsError as exc:
        print(f"  ! constituent list unavailable, roundup detection off: {exc}")
        return build_index([])
    return build_index((c.symbol, c.name) for c in universe)


async def scan_ticker(
    ticker: str,
    client: httpx.AsyncClient,
    *,
    filing_lookback_days: int = 30,
    names: NameIndex | None = None,
) -> ScanReport | None:
    """Run one full pass. Returns None if the ticker cannot be resolved."""
    from scanner.llm.triage import triage

    entity = await resolve(ticker, client)
    if entity is None:
        print(f"  ! {ticker.upper()} is not in the SEC ticker universe")
        return None

    # --- L1: ingest -----------------------------------------------------
    try:
        fetched = await yahoo_rss.fetch(entity.ticker, client)
    except FeedError as exc:
        print(f"  ! news feed unavailable: {exc}")
        fetched = ()

    try:
        since = (datetime.now(timezone.utc) - timedelta(days=filing_lookback_days)).date()
        filings = await edgar.fetch(entity, client, since=since)
    except EdgarError as exc:
        print(f"  ! EDGAR unavailable: {exc}")
        filings = ()

    # --- L2: deterministic prefilter (free, and does the heavy lifting) --
    candidates = prefilter(fetched, entity)

    # --- L2b: roundups — about everybody, therefore about nobody ---------
    index = names if names is not None else await load_name_index(client)
    candidates, roundups = drop_roundups(candidates, index)
    for item in roundups:
        print(f"  . roundup, not news about {entity.ticker}: {item.title[:70]}")

    # --- L3: dedup ------------------------------------------------------
    try:
        deduped = await deduplicate(candidates, client)
    except LLMError as exc:
        print(f"  ! dedup unavailable, continuing undeduped: {exc}")
        deduped = candidates

    # --- L4: the model only sees survivors ------------------------------
    triaged = (
        await triage(entity.ticker, entity.official_name, deduped, client)
        if deduped
        else ()
    )
    kept = tuple(t for t in triaged if t.verdict.keeps)

    return ScanReport(
        ticker=entity.ticker,
        company=entity.official_name,
        fetched=len(fetched),
        after_prefilter=len(candidates) + len(roundups),
        after_roundup=len(candidates),
        after_dedup=len(deduped),
        kept=len(kept),
        items=kept,
        filings=filings,
        generated_at=datetime.now(timezone.utc),
    )
