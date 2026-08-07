"""Official SEC ticker -> CIK -> company-name mapping.

Source: https://www.sec.gov/files/company_tickers.json (free, no key).

This is the free equivalent of what commercial vendors call an "entity master".
It is the authoritative answer to "which company does this symbol mean?", and
it is what stops us trusting a headline that writes NVDA when it means NVAX.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from scanner.config import settings

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CACHE_PATH = Path("data/company_tickers.json")


class EntityMasterError(RuntimeError):
    """Raised when the ticker universe cannot be loaded."""


async def _download(client: httpx.AsyncClient) -> dict:
    try:
        response = await client.get(
            SEC_TICKERS_URL,
            # SEC blocks generic agents; a contact string is mandatory policy.
            headers={"User-Agent": settings.sec_user_agent},
            timeout=settings.http_timeout_s,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise EntityMasterError(f"could not fetch SEC ticker file: {exc}") from exc


async def load_universe(
    client: httpx.AsyncClient, *, refresh: bool = False
) -> dict[str, tuple[int, str]]:
    """Return {TICKER: (cik, official_name)}, cached on disk.

    The file changes slowly, so we cache aggressively rather than hammering
    SEC on every run.
    """
    if not refresh and CACHE_PATH.exists():
        try:
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = await _download(client)  # corrupt cache — refetch
    else:
        raw = await _download(client)
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(raw), encoding="utf-8")

    universe: dict[str, tuple[int, str]] = {}
    for entry in raw.values():
        ticker = str(entry.get("ticker", "")).upper().strip()
        name = str(entry.get("title", "")).strip()
        cik = entry.get("cik_str")
        if ticker and name and isinstance(cik, int):
            universe[ticker] = (cik, name)

    if not universe:
        raise EntityMasterError("SEC ticker file parsed to an empty universe")
    return universe
