"""Roundup-detector tests.

Every headline quoted here is real, taken from Yahoo's feeds on 2026-08-05.
The two marked LIVE FALSE POSITIVE are the ones Gemma 3 passed as material
subject news for NVDA — the failure this module exists to make impossible.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scanner.models import NewsItem
from scanner.roundup import (
    build_index,
    companies_named,
    drop_roundups,
    is_roundup,
)

COMPANIES = [
    ("NVDA", "NVIDIA Corp"),
    ("PLTR", "Palantir Technologies Inc."),
    ("GOOGL", "Alphabet Inc. Class A"),
    ("META", "Meta Platforms, Inc."),
    ("WDC", "Western Digital Corporation"),
    ("SNDK", "Sandisk Corp"),
    ("AAPL", "Apple Inc."),
    ("AMZN", "Amazon.com, Inc."),
    ("FSLR", "First Solar, Inc."),
    ("BBY", "Best Buy Co., Inc."),
    ("TTD", "The Trade Desk, Inc."),
    ("LYV", "Live Nation Entertainment"),
    ("AMD", "Advanced Micro Devices, Inc."),
]
INDEX = build_index(COMPANIES)


def _item(title: str, summary: str = "") -> NewsItem:
    return NewsItem(
        id=title, source="yahoo_rss", queried_ticker="NVDA",
        title=title, summary=summary,
        fetched_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )


# ---- the live failures -------------------------------------------------

def test_multi_company_listicle_is_a_roundup():
    """LIVE FALSE POSITIVE: Gemma 3 called this material subject news."""
    item = _item(
        "AI Trailblazers Nvidia, Palantir, Alphabet, and Meta Are Shaking "
        "Wall Street to Its Core With This $3.4 Billion Warning"
    )
    assert companies_named(item.title, INDEX) == frozenset({"NVDA", "PLTR", "GOOGL", "META"})
    assert is_roundup(item, INDEX)


def test_market_wrap_is_a_roundup():
    """LIVE FALSE POSITIVE: same scan, same verdict."""
    item = _item(
        "Dow Jones Futures: Nvidia Lifts Dow, But Google, SpaceX Skid; "
        "Sandisk, Western Digital Lead Earnings Losers Late"
    )
    assert is_roundup(item, INDEX)


# ---- what must survive -------------------------------------------------

def test_single_company_story_survives():
    item = _item("Apple's Quarterly iPhone Sales Were Even Better Than They Seem")
    assert companies_named(item.title, INDEX) == frozenset({"AAPL"})
    assert not is_roundup(item, INDEX)


def test_two_company_comparison_survives():
    """Two names is a comparison, not a roundup — the model judges those."""
    item = _item("Amazon and Alphabet Just Crushed Expectations. Which Is the Better Buy?")
    assert not is_roundup(item, INDEX)


# ---- the ambiguous-word failures ---------------------------------------
#
# Each of these matched a company on a real headline before the ambiguous
# single-word names were excluded from the vocabulary.

@pytest.mark.parametrize(
    ("title", "ghost"),
    [
        ("Jeff Bezos Unloads $350M Worth Of Amazon Stock In First Stake Sale", "FSLR"),
        ("Tim Cook's Last Quarter as CEO Was Apple's Best June Quarter Ever", "BBY"),
        ("JPMorgan Says Bet on the Best AI Trade of the Year", "TTD"),
        ("Stock Market Today: Dow Rises To Record (Live Coverage)", "LYV"),
    ],
)
def test_ordinary_english_does_not_conjure_companies(title, ghost):
    assert ghost not in companies_named(title, INDEX)


def test_multiword_name_still_matches_its_company():
    """Excluding "first" as a token must not lose First Solar itself."""
    assert "FSLR" in companies_named("First Solar beats on margins", INDEX)
    assert "BBY" in companies_named("Best Buy cuts guidance", INDEX)


def test_longest_match_wins():
    """Western Digital is one company, not "western" plus "digital"."""
    assert companies_named("Western Digital raises guidance", INDEX) == frozenset({"WDC"})


# ---- ticker forms ------------------------------------------------------

def test_bare_tickers_count():
    item = _item("JPMorgan Says Bet NVDA, AMD, but Morgan Stanley Backs META")
    assert is_roundup(item, INDEX)


def test_unknown_tickers_are_ignored():
    assert companies_named("XYZQ and ABCD surge on nothing", INDEX) == frozenset()


# ---- splitting ---------------------------------------------------------

def test_drop_roundups_partitions_without_losing_items():
    items = (
        _item("Apple's Quarterly iPhone Sales Were Even Better Than They Seem"),
        _item("Nvidia, Palantir and Meta Lead the AI Trade Higher"),
    )
    kept, dropped = drop_roundups(items, INDEX)
    assert len(kept) == 1 and len(dropped) == 1
    assert kept[0].title.startswith("Apple")


def test_threshold_is_adjustable():
    item = _item("Amazon and Alphabet Just Crushed Expectations")
    assert not is_roundup(item, INDEX)
    assert is_roundup(item, INDEX, threshold=2)


def test_summary_tail_lists_do_not_flag_a_real_story():
    """Summaries drift into "other movers" lists; only the title is counted."""
    item = _item(
        "Nvidia Raises Full-Year Guidance",
        "Also moving today: Palantir, Meta, Alphabet and Western Digital.",
    )
    assert not is_roundup(item, INDEX)


def test_empty_index_flags_nothing():
    empty = build_index([])
    assert companies_named("Nvidia, Palantir and Meta", empty) == frozenset()
