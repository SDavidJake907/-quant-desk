"""The anti-hallucination guard is the most important thing in this repo.

If the model paraphrases or invents its evidence, the verdict must be dropped.

It is not enough for a quote to exist, either. Copying the headline satisfies
verbatim-occurrence perfectly while justifying nothing, and that is exactly
what the model does when it has no real reason for its answer.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scanner.llm.grounding import (
    Rejection,
    is_grounded,
    is_substantive,
    is_wholesale_copy,
    occurs_verbatim,
    reject_reason,
)
from scanner.models import NewsItem


def _item(title: str, summary: str = "") -> NewsItem:
    return NewsItem(
        id="x", source="yahoo_rss", queried_ticker="NVDA",
        title=title, summary=summary,
        fetched_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )


ITEM = _item("Nvidia Raises Full-Year Guidance", "The company cited data center demand.")


# ---- does the quote exist? ---------------------------------------------

def test_accepts_exact_span_from_title():
    assert is_grounded("Nvidia Raises Full-Year Guidance", ITEM)


def test_accepts_exact_span_from_summary():
    assert is_grounded("The company cited data center demand", ITEM)


def test_is_case_insensitive():
    assert is_grounded("nvidia raises full-year guidance", ITEM)


def test_rejects_paraphrase():
    assert reject_reason("Nvidia increased its outlook for the year", ITEM) == Rejection.NOT_VERBATIM


def test_rejects_fabricated_numbers():
    assert not is_grounded("guidance raised to $5.2 billion this year", ITEM)


def test_rejects_empty_quote():
    assert reject_reason("   ", ITEM) == Rejection.EMPTY


# ---- is the quote a citation? ------------------------------------------

def test_rejects_a_quote_that_only_names_the_company():
    """LIVE FAILURE: "AI Trailblazers Nvidia" was accepted as the reason a
    four-company listicle was subject news about Nvidia."""
    item = _item(
        "AI Trailblazers Nvidia, Palantir, Alphabet, and Meta Are Shaking "
        "Wall Street to Its Core"
    )
    assert occurs_verbatim("AI Trailblazers Nvidia", item)  # perfectly verbatim
    assert reject_reason("AI Trailblazers Nvidia", item) == Rejection.TOO_SHORT


def test_rejects_a_bare_fragment():
    """LIVE FAILURE: "Nvidia Lifts Dow" justified a market wrap as subject news."""
    item = _item("Dow Jones Futures: Nvidia Lifts Dow, But Google, SpaceX Skid")
    assert reject_reason("Nvidia Lifts Dow", item) == Rejection.TOO_SHORT


def test_rejects_copying_the_whole_source():
    long_item = _item(
        "Tim Cook's Last Quarter as CEO Was Apple's Best June Quarter Ever",
        "The stock fell anyway, as investors looked past the headline numbers.",
    )
    whole = f"{long_item.title} {long_item.summary}"
    assert occurs_verbatim(whole, long_item)
    assert reject_reason(whole, long_item) == Rejection.WHOLESALE_COPY


def test_short_headline_may_be_quoted_whole():
    """With no summary and a terse headline there is no smaller span to cite;
    refusing it would drop real news for having nothing else to say."""
    terse = _item("Nvidia Raises Full-Year Guidance")
    assert is_grounded("Nvidia Raises Full-Year Guidance", terse)
    assert not is_wholesale_copy("Nvidia Raises Full-Year Guidance", terse)


def test_substantive_needs_both_words_and_length():
    assert not is_substantive("Nvidia")
    assert not is_substantive("Nvidia Lifts Dow")
    assert not is_substantive("a b c d")           # four words, too short
    assert is_substantive("Nvidia raises its full-year guidance")


def test_a_real_span_from_a_long_article_passes_everything():
    item = _item(
        "Departing Apple CEO Tim Cook Just Gave Memory Stocks Good News",
        "Cook told investors that memory prices will continue to rise.",
    )
    assert is_grounded("memory prices will continue to rise", item)
