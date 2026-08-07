"""Roundup detection — throwing out articles that are about everybody.

"AI Trailblazers Nvidia, Palantir, Alphabet, and Meta Are Shaking Wall Street"
is not news about Nvidia. It is a listicle that happens to contain Nvidia, and
it is the single most common false positive in a per-ticker news feed.

The triage prompt already asks the model to call these `example` rather than
`subject`. Live on 2026-08-05 it did not: both roundups in an NVDA scan came
back as material subject news. Counting company names is deterministic,
free, unit-testable and does not vary run to run, so it belongs in front of
the model rather than inside it — the same reasoning that puts the alias
prefilter before triage.

WHY THE VOCABULARY IS THE INDEX, NOT ALL OF EDGAR
Matching against all 10,398 SEC registrants sounds stricter and is much worse:
their names include "Here", "Better", "Rise" and "Lead", so ordinary English
headlines light up with phantom companies. Measured on 200 live headlines,
the SEC vocabulary flagged 45 as roundups, most of them wrongly; the index
vocabulary flagged 13, nearly all correctly. The universe we actually screen
is the S&P 500, so that is the right list to count against.
"""

from __future__ import annotations

import re
from typing import Iterable

from scanner.entity import _core_name
from scanner.models import Frozen, NewsItem

#: Companies named in one article, at or above which it is a roundup rather
#: than a story. Two is a comparison ("X vs Y"), which the model handles.
DEFAULT_THRESHOLD = 3

# Single-word company names that are also ordinary English. Left in, "first"
# matches First Solar in "First Stake Sale", "best" matches Best Buy in "Best
# June Quarter Ever", and "trade" matches Trade Desk in "Best AI Trade" — all
# observed on real headlines. These companies keep their multi-word aliases
# ("first solar", "best buy", "trade desk"), which is how they should be
# matched anyway; single-word names like Target simply go uncounted.
_AMBIGUOUS_TOKENS = frozenset({
    "advanced", "align", "applied", "arch", "archer", "ball", "bank", "best",
    "block", "boston", "capital", "casey", "charter", "church", "comfort",
    "consolidated", "cooper", "crown", "delta", "digital", "dover", "duke",
    "eaton", "electronic", "equity", "erie", "everest", "expand", "extra",
    "fair", "federal", "fifth", "first", "flex", "ford", "franklin", "genuine",
    "global", "globe", "henry", "home", "host", "illinois", "interactive",
    "invitation", "iron", "jack", "jacobs", "live", "marathon", "martin",
    "monster", "moody", "morgan", "northern", "norwegian", "packaging",
    "paramount", "parker", "philip", "phillips", "pinnacle", "principal",
    "quest", "ralph", "realty", "regency", "regions", "republic", "ross",
    "royal", "simon", "snap", "southern", "southwest", "stanley", "state",
    "steel", "take", "target", "trade", "union", "universal", "walt", "waste",
    "waters", "west", "western",
})

_WORD = re.compile(r"[a-z0-9&]+")
#: Bare uppercase tickers in a title, as in "Bet NVDA, AMD, but MSFT".
_TICKER = re.compile(r"(?<![A-Za-z0-9$])\$?([A-Z]{2,5})(?![A-Za-z0-9])")
_MAX_PHRASE_WORDS = 5


class NameIndex(Frozen):
    """Company names to count against, keyed for longest-match-first lookup."""

    by_phrase: dict[str, str]  # "western digital" -> WDC
    by_token: dict[str, str]   # "nvidia" -> NVDA
    tickers: frozenset[str]

    def __len__(self) -> int:
        return len(self.tickers)


def build_index(companies: Iterable[tuple[str, str]]) -> NameIndex:
    """Build the lookup from (symbol, official name) pairs."""
    by_phrase: dict[str, str] = {}
    tickers: set[str] = set()
    token_owners: dict[str, set[str]] = {}

    for symbol, name in companies:
        symbol = symbol.upper().strip()
        core = _core_name(name)
        if not symbol or not core:
            continue
        tickers.add(symbol)
        by_phrase[core] = symbol
        head = core.split(" ")[0]
        if len(head) > 3 and head not in _AMBIGUOUS_TOKENS:
            token_owners.setdefault(head, set()).add(symbol)

    # A head word shared by two companies identifies neither of them.
    by_token = {
        token: next(iter(owners))
        for token, owners in token_owners.items()
        if len(owners) == 1
    }
    return NameIndex(by_phrase=by_phrase, by_token=by_token, tickers=frozenset(tickers))


def companies_named(text: str, index: NameIndex) -> frozenset[str]:
    """Distinct index members named in `text`.

    Longest phrase wins: "Western Digital" must count once as WDC, not twice
    as "western" plus "digital".
    """
    words = _WORD.findall(text.lower())
    found: set[str] = set()
    used = [False] * len(words)

    for size in range(_MAX_PHRASE_WORDS, 1, -1):
        for i in range(len(words) - size + 1):
            if any(used[i : i + size]):
                continue
            symbol = index.by_phrase.get(" ".join(words[i : i + size]))
            if symbol:
                found.add(symbol)
                used[i : i + size] = [True] * size

    for i, word in enumerate(words):
        if not used[i]:
            symbol = index.by_token.get(word)
            if symbol:
                found.add(symbol)

    # Tickers are written in caps, so match them against the original casing.
    found |= {t for t in _TICKER.findall(text) if t in index.tickers}
    return frozenset(found)


def is_roundup(
    item: NewsItem, index: NameIndex, *, threshold: int = DEFAULT_THRESHOLD
) -> bool:
    """True when the title names `threshold` or more index members.

    Only the TITLE is counted. Summaries wander into "other movers today" tail
    lists, which would flag ordinary single-company stories as roundups.
    """
    return len(companies_named(item.title, index)) >= threshold


def drop_roundups(
    items: tuple[NewsItem, ...],
    index: NameIndex,
    *,
    threshold: int = DEFAULT_THRESHOLD,
) -> tuple[tuple[NewsItem, ...], tuple[NewsItem, ...]]:
    """Split items into (kept, dropped). Dropped items never reach the model."""
    kept: list[NewsItem] = []
    dropped: list[NewsItem] = []
    for item in items:
        (dropped if is_roundup(item, index, threshold=threshold) else kept).append(item)
    return tuple(kept), tuple(dropped)
