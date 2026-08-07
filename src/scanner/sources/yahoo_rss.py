"""Yahoo Finance RSS ingestion.

Endpoint (verified live 2026-08-05, HTTP 200, no auth):
    https://finance.yahoo.com/rss/headline?s=<TICKER>

Caveat measured empirically: this feed is only loosely ticker-filtered. An NVDA
pull returned 14 headlines of which 1 mentioned Nvidia. Treat everything from
here as a candidate pool, never as ticker-specific news. The triage layer is
what makes it usable.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import httpx
from defusedxml import ElementTree  # untrusted XML — guards against XXE / entity bombs

from scanner.config import settings
from scanner.models import NewsItem

FEED_URL = "https://finance.yahoo.com/rss/headline"


class FeedError(RuntimeError):
    """Raised when a feed cannot be fetched or parsed."""


def _text(node, tag: str) -> str:
    child = node.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _parse_date(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def _stable_id(guid: str, link: str, title: str) -> str:
    """Prefer the feed's GUID; fall back to a hash so IDs stay deterministic."""
    if guid:
        return guid
    basis = link or title
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def parse_feed(xml: str, ticker: str, fetched_at: datetime) -> tuple[NewsItem, ...]:
    """Pure parse step — no I/O, so it is trivially unit-testable."""
    try:
        root = ElementTree.fromstring(xml)
    except Exception as exc:  # defusedxml raises several distinct types
        raise FeedError(f"malformed RSS for {ticker}: {exc}") from exc

    items = []
    for node in root.iter("item"):
        title = _text(node, "title")
        if not title:
            continue  # a headline-less item carries no signal
        items.append(
            NewsItem(
                id=_stable_id(_text(node, "guid"), _text(node, "link"), title),
                source="yahoo_rss",
                queried_ticker=ticker.upper(),
                title=title,
                summary=_text(node, "description"),
                url=_text(node, "link"),
                published_at=_parse_date(_text(node, "pubDate")),
                fetched_at=fetched_at,
            )
        )
    return tuple(items)


async def fetch(ticker: str, client: httpx.AsyncClient) -> tuple[NewsItem, ...]:
    """Fetch and parse one ticker's feed. Raises FeedError on any failure."""
    try:
        response = await client.get(
            FEED_URL,
            params={"s": ticker.upper()},
            headers={"User-Agent": settings.user_agent},
            timeout=settings.http_timeout_s,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FeedError(f"fetch failed for {ticker}: {exc}") from exc

    return parse_feed(response.text, ticker, datetime.now(timezone.utc))
