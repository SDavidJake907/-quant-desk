"""EDGAR parsing tests — the SEC classifies events for us, so verify we read
those codes correctly rather than inventing our own taxonomy."""

from __future__ import annotations

from datetime import date, datetime, timezone

from scanner.entity import build_entity
from scanner.sources.edgar import HIGH_SEVERITY_ITEMS, parse_submissions

NVDA = build_entity("NVDA", 1045810, "NVIDIA CORP")
FETCHED_AT = datetime(2026, 8, 5, tzinfo=timezone.utc)

PAYLOAD = {
    "filings": {
        "recent": {
            "form": ["8-K", "4", "8-K", "8-K", "SCHEDULE 13G"],
            "filingDate": ["2026-07-02", "2026-07-06", "2026-06-30", "2026-01-15", "2026-07-20"],
            "items": ["5.02", "", "2.02,7.01", "4.02", ""],
            "accessionNumber": ["0001-26-001", "x", "0001-26-002", "0001-26-003", "y"],
            "primaryDocument": ["a.htm", "x.htm", "b.htm", "c.htm", "y.htm"],
        }
    }
}


def test_extracts_only_8k_forms():
    items = parse_submissions(PAYLOAD, NVDA, FETCHED_AT)
    assert len(items) == 3
    assert all(i.source == "sec_edgar_8k" for i in items)


def test_translates_item_codes_to_plain_english():
    items = parse_submissions(PAYLOAD, NVDA, FETCHED_AT)
    assert "director or officer change" in items[0].summary


def test_handles_multiple_item_codes():
    items = parse_submissions(PAYLOAD, NVDA, FETCHED_AT)
    earnings = next(i for i in items if "2.02" in i.summary)
    assert "earnings" in earnings.summary
    assert "7.01" in earnings.summary


def test_since_filter_excludes_older_filings():
    items = parse_submissions(PAYLOAD, NVDA, FETCHED_AT, since=date(2026, 6, 1))
    assert len(items) == 2
    assert all(i.published_at and i.published_at.date() >= date(2026, 6, 1) for i in items)


def test_builds_archive_url():
    items = parse_submissions(PAYLOAD, NVDA, FETCHED_AT)
    assert items[0].url.startswith("https://www.sec.gov/Archives/edgar/data/1045810/")


def test_non_reliance_is_flagged_high_severity():
    assert "4.02" in HIGH_SEVERITY_ITEMS


def test_empty_payload_is_safe():
    assert parse_submissions({}, NVDA, FETCHED_AT) == ()
