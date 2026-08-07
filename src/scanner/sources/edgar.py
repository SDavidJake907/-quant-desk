"""SEC EDGAR filings ingestion — the zero-noise news source.

Endpoint: https://data.sec.gov/submissions/CIK##########.json (free, no key,
10 req/s limit, contact User-Agent required).

Why this beats every news feed: an 8-K is a legally mandated disclosure of a
material event, filed by the company itself. There is no editorial noise, no
ticker confusion, and no clickbait. Better still, the SEC tags each 8-K with
item codes, so the event TYPE is already classified for us — deterministically,
with no model involved.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx

from scanner.config import settings
from scanner.entity import Entity
from scanner.models import NewsItem

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# The subset of 8-K items that actually move a stock, with plain-English names.
MATERIAL_8K_ITEMS: dict[str, str] = {
    "1.01": "entered a material agreement",
    "1.02": "terminated a material agreement",
    "1.03": "entered bankruptcy or receivership",
    "2.01": "completed an acquisition or disposition",
    "2.02": "reported results of operations (earnings)",
    "2.03": "took on a material financial obligation",
    "2.04": "triggered acceleration of a financial obligation",
    "2.05": "committed to exit or disposal costs",
    "2.06": "recorded a material impairment",
    "3.01": "received a delisting or listing-rule notice",
    "4.01": "changed its certifying accountant",
    "4.02": "said prior financial statements cannot be relied upon",
    "5.02": "announced a director or officer change",
    "7.01": "made a Regulation FD disclosure",
    "8.01": "disclosed another material event",
}

# Items that are almost always red flags, worth surfacing loudly.
HIGH_SEVERITY_ITEMS = frozenset({"1.03", "2.06", "3.01", "4.02"})


class EdgarError(RuntimeError):
    """Raised when EDGAR cannot be reached or parsed."""


def _describe(items_field: str) -> tuple[str, ...]:
    """Turn '5.02,7.01' into readable event descriptions."""
    codes = [c.strip() for c in items_field.split(",") if c.strip()]
    return tuple(
        f"{code} — {MATERIAL_8K_ITEMS[code]}"
        for code in codes
        if code in MATERIAL_8K_ITEMS
    )


def parse_submissions(
    payload: dict, entity: Entity, fetched_at: datetime, *, since: date | None = None
) -> tuple[NewsItem, ...]:
    """Pure parse step: extract material 8-K filings as NewsItems."""
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    if not forms:
        return ()

    dates = recent.get("filingDate", [])
    items = recent.get("items", [])
    accessions = recent.get("accessionNumber", [])
    documents = recent.get("primaryDocument", [])

    results: list[NewsItem] = []
    for i, form in enumerate(forms):
        if form != "8-K":
            continue

        filed_raw = dates[i] if i < len(dates) else ""
        try:
            filed = date.fromisoformat(filed_raw)
        except ValueError:
            continue  # undated filing is unusable for point-in-time work

        if since and filed < since:
            continue

        descriptions = _describe(items[i] if i < len(items) else "")
        if not descriptions:
            continue  # only routine/unclassified items — no signal

        accession = accessions[i] if i < len(accessions) else ""
        document = documents[i] if i < len(documents) else ""
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{entity.cik}/"
            f"{accession.replace('-', '')}/{document}"
            if accession and document
            else ""
        )

        results.append(
            NewsItem(
                id=f"edgar:{accession}",
                source="sec_edgar_8k",
                queried_ticker=entity.ticker,
                title=f"{entity.official_name} filed an 8-K: {descriptions[0]}",
                summary="; ".join(descriptions),
                url=url,
                published_at=datetime(
                    filed.year, filed.month, filed.day, tzinfo=timezone.utc
                ),
                fetched_at=fetched_at,
            )
        )
    return tuple(results)


async def fetch(
    entity: Entity, client: httpx.AsyncClient, *, since: date | None = None
) -> tuple[NewsItem, ...]:
    """Fetch recent material 8-K filings for a resolved entity."""
    try:
        response = await client.get(
            SUBMISSIONS_URL.format(cik=entity.cik_padded),
            headers={"User-Agent": settings.sec_user_agent},
            timeout=settings.http_timeout_s,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise EdgarError(f"EDGAR fetch failed for {entity.ticker}: {exc}") from exc

    return parse_submissions(payload, entity, datetime.now(timezone.utc), since=since)
