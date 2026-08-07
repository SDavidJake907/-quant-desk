"""S&P 500 constituent list, including GICS sector.

Free CSV, no key. Sector matters: comparing a utility to the whole index tells
you less than comparing it to other utilities.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import httpx

from scanner.config import settings
from scanner.models import Frozen

CONSTITUENTS_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
    "main/data/constituents.csv"
)
CACHE_PATH = Path("data/constituents.csv")


class ConstituentsError(RuntimeError):
    """Raised when the universe cannot be loaded."""


class Constituent(Frozen):
    symbol: str
    name: str
    sector: str
    cik: str = ""


def deduplicate_share_classes(rows: tuple[Constituent, ...]) -> tuple[Constituent, ...]:
    """Collapse multiple share classes of the same company.

    GOOGL and GOOG are one company with two listings. Ranking both is
    double-counting: it inflates a single bet into two "ideas" and quietly
    concentrates the portfolio. CIK identifies the filer, so it groups share
    classes correctly where a name-match would not.
    """
    seen: set[str] = set()
    kept: list[Constituent] = []
    for row in rows:
        key = row.cik or f"name:{row.name.lower()}"
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return tuple(kept)


def parse_csv(text: str, *, dedupe: bool = True) -> tuple[Constituent, ...]:
    reader = csv.DictReader(io.StringIO(text))
    rows = [
        Constituent(
            symbol=(row.get("Symbol") or "").strip().upper(),
            name=(row.get("Security") or "").strip(),
            sector=(row.get("GICS Sector") or "").strip(),
            cik=(row.get("CIK") or "").strip(),
        )
        for row in reader
    ]
    valid = tuple(r for r in rows if r.symbol)
    if not valid:
        raise ConstituentsError("constituent file parsed to zero rows")
    return deduplicate_share_classes(valid) if dedupe else valid


async def load(client: httpx.AsyncClient, *, refresh: bool = False) -> tuple[Constituent, ...]:
    """Load the universe, caching to disk."""
    if not refresh and CACHE_PATH.exists():
        return parse_csv(CACHE_PATH.read_text(encoding="utf-8"))

    try:
        response = await client.get(
            CONSTITUENTS_URL,
            headers={"User-Agent": settings.user_agent},
            timeout=settings.http_timeout_s,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ConstituentsError(f"could not fetch constituents: {exc}") from exc

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(response.text, encoding="utf-8")
    return parse_csv(response.text)
