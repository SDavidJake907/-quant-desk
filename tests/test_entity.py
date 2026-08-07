"""Entity-resolution tests.

The Novavax case is a real bug caught in a live run on 2026-08-05: Yahoo's NVDA
feed served an article titled "Novavax (NVDA) ..." and the model accepted it
because the literal string NVDA appeared. Novavax is NVAX. That regression is
pinned below.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scanner.entity import build_entity, mentions_entity, prefilter
from scanner.models import NewsItem

NVDA = build_entity("NVDA", 1045810, "NVIDIA CORP")
AAPL = build_entity("AAPL", 320193, "Apple Inc.")
AMD = build_entity("AMD", 2488, "Advanced Micro Devices, Inc.")


def _item(title: str, summary: str = "") -> NewsItem:
    return NewsItem(
        id=title,
        source="test",
        queried_ticker="NVDA",
        title=title,
        summary=summary,
        fetched_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )


def test_strips_corporate_suffixes_from_aliases():
    assert "nvidia" in NVDA.aliases
    assert "advanced micro devices" in AMD.aliases


def test_pads_cik_for_edgar():
    assert NVDA.cik_padded == "0001045810"


def test_matches_company_name():
    assert mentions_entity("Nvidia raises guidance", NVDA)
    assert mentions_entity("Apple's India sales cross $10 billion", AAPL)


def test_rejects_bare_ticker_without_name():
    """The Novavax regression: NVDA appears, NVIDIA does not."""
    title = "Novavax (NVDA) Could Be 44% Undervalued Following Its Licensing Pivot"
    assert not mentions_entity(title, NVDA)


def test_rejects_unrelated_company():
    assert not mentions_entity("Where Will Bitcoin Be in 3 Years?", NVDA)


def test_rejects_supplier_story_for_wrong_ticker():
    """'Nvidia Partner Hon Hai' is not an Apple story."""
    assert not mentions_entity("Nvidia Partner Hon Hai's Sales Rise 54%", AAPL)


def test_word_boundaries_prevent_substring_hits():
    assert not mentions_entity("The applesauce market is growing", AAPL)


def test_prefilter_keeps_only_named_items():
    items = (
        _item("Nvidia B200 systems are sold out"),
        _item("Novavax (NVDA) Could Be 44% Undervalued"),
        _item("Where Will Bitcoin Be in 3 Years?"),
        _item("Chipmaker update", "Nvidia shipped more units"),
    )
    kept = prefilter(items, NVDA)
    assert len(kept) == 2
    assert all("ovavax" not in i.title for i in kept)


def test_prefilter_is_immutable_and_returns_new_tuple():
    items = (_item("Nvidia news"),)
    assert prefilter(items, NVDA) is not items
