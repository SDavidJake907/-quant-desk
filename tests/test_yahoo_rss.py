"""Parser tests — pure functions, no network."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scanner.sources.yahoo_rss import FeedError, parse_feed

FETCHED_AT = datetime(2026, 8, 5, 16, 0, tzinfo=timezone.utc)

# Trimmed from a real live response.
SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <description>Generate Biomedicines focuses on AI-enabled drug discovery.</description>
    <guid isPermaLink="false">d2bb3887-cffc-35c1-917b-4e3315bd4904</guid>
    <link>https://www.fool.com/investing/2026/08/05/jensen-huang-biotech/</link>
    <pubDate>Wed, 05 Aug 2026 15:50:00 +0000</pubDate>
    <title>This Is Nvidia CEO Jensen Huang's Favorite Biotech Stock</title>
  </item>
  <item>
    <title>Where Will Bitcoin Be in 3 Years?</title>
    <link>https://example.com/bitcoin</link>
    <pubDate>Wed, 05 Aug 2026 14:00:00 +0000</pubDate>
  </item>
  <item>
    <description>No title, should be skipped.</description>
  </item>
</channel></rss>"""


def test_parses_items_and_skips_titleless():
    items = parse_feed(SAMPLE, "nvda", FETCHED_AT)
    assert len(items) == 2


def test_uses_guid_as_id_when_present():
    items = parse_feed(SAMPLE, "NVDA", FETCHED_AT)
    assert items[0].id == "d2bb3887-cffc-35c1-917b-4e3315bd4904"


def test_falls_back_to_deterministic_hash_without_guid():
    first = parse_feed(SAMPLE, "NVDA", FETCHED_AT)[1]
    second = parse_feed(SAMPLE, "NVDA", FETCHED_AT)[1]
    assert first.id == second.id
    assert len(first.id) == 32


def test_normalises_ticker_and_stamps_fetch_time():
    item = parse_feed(SAMPLE, "nvda", FETCHED_AT)[0]
    assert item.queried_ticker == "NVDA"
    assert item.fetched_at == FETCHED_AT


def test_parses_publication_date():
    item = parse_feed(SAMPLE, "NVDA", FETCHED_AT)[0]
    assert item.published_at is not None
    assert item.published_at.year == 2026


def test_items_are_immutable():
    item = parse_feed(SAMPLE, "NVDA", FETCHED_AT)[0]
    with pytest.raises(Exception):
        item.title = "mutated"


def test_malformed_xml_raises_feed_error():
    with pytest.raises(FeedError):
        parse_feed("<rss><channel><item>", "NVDA", FETCHED_AT)
