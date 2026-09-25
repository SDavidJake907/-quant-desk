"""Point-in-time headlines, and the properties that make them worth keeping.

The store exists so a DECISION can be reproduced. That needs three things it
did not have before: the headlines survive the scan, re-reading them fourteen
times a day does not bloat the file, and a storage failure never reaches the
trade.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from scanner.models import NewsItem
from scanner.sources import news_store

DAY = date(2026, 9, 25)


def _item(item_id: str, ticker: str = "ACN", title: str = "A headline") -> NewsItem:
    return NewsItem(
        id=item_id, source="yahoo_rss", queried_ticker=ticker,
        title=title, summary="Some summary text.", url=f"http://x/{item_id}",
        fetched_at=datetime(2026, 9, 25, 13, 30, tzinfo=timezone.utc),
    )


def test_items_survive_the_scan(tmp_path):
    assert news_store.append_items([_item("a"), _item("b")], on=DAY, root=tmp_path) == 2
    assert {i.id for i in news_store.load_day(DAY, tmp_path)} == {"a", "b"}


def test_rereading_the_same_story_does_not_grow_the_file(tmp_path):
    """A scan runs fourteen times a day over largely the same feed. The file
    should grow with stories, not with scans."""
    news_store.append_items([_item("a")], on=DAY, root=tmp_path)
    for _ in range(13):
        assert news_store.append_items([_item("a")], on=DAY, root=tmp_path) == 0
    assert len(news_store.load_day(DAY, tmp_path)) == 1


def test_a_new_story_is_still_added_to_an_existing_day(tmp_path):
    news_store.append_items([_item("a")], on=DAY, root=tmp_path)
    assert news_store.append_items([_item("a"), _item("c")], on=DAY, root=tmp_path) == 1
    assert len(news_store.load_day(DAY, tmp_path)) == 2


def test_the_point_in_time_stamp_is_preserved(tmp_path):
    """Without fetched_at the store cannot say what was visible WHEN."""
    news_store.append_items([_item("a")], on=DAY, root=tmp_path)
    restored = news_store.load_day(DAY, tmp_path)[0]
    assert restored.fetched_at == datetime(2026, 9, 25, 13, 30, tzinfo=timezone.utc)


def test_one_symbol_can_be_read_back(tmp_path):
    news_store.append_items(
        [_item("a", "ACN"), _item("b", "PSX"), _item("c", "ACN")],
        on=DAY, root=tmp_path,
    )
    assert {i.id for i in news_store.load_symbol("ACN", DAY, tmp_path)} == {"a", "c"}


def test_days_are_kept_apart(tmp_path):
    other = date(2026, 9, 26)
    news_store.append_items([_item("a")], on=DAY, root=tmp_path)
    news_store.append_items([_item("b")], on=other, root=tmp_path)
    assert {i.id for i in news_store.load_day(DAY, tmp_path)} == {"a"}
    assert {i.id for i in news_store.load_day(other, tmp_path)} == {"b"}


def test_an_unreadable_store_never_reaches_the_trade(tmp_path):
    """These headlines are evidence ABOUT a decision, not an input to it. A
    storage failure must cost the evidence, never the trade."""
    blocked = tmp_path / "news.jsonl"
    blocked.write_text("not a directory", encoding="utf-8")
    assert news_store.append_items([_item("a")], on=DAY, root=blocked) == 0


def test_a_damaged_line_does_not_lose_the_rest_of_the_day(tmp_path):
    news_store.append_items([_item("a")], on=DAY, root=tmp_path)
    path = news_store.day_path(DAY, tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{ not json\n")
    news_store.append_items([_item("b")], on=DAY, root=tmp_path)
    assert {i.id for i in news_store.load_day(DAY, tmp_path)} == {"a", "b"}


def test_an_empty_batch_writes_nothing(tmp_path):
    assert news_store.append_items([], on=DAY, root=tmp_path) == 0
    assert news_store.load_day(DAY, tmp_path) == ()


def test_a_missing_day_reads_as_empty_not_an_error(tmp_path):
    assert news_store.load_day(date(2026, 1, 1), tmp_path) == ()
