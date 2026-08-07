"""Watchlist tests.

The watchlist exists for one distinction: a refusal that will expire versus
one that will not. Getting that wrong either loses good setups forever or
revives setups whose levels no longer describe anything.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from scanner.execution.watchlist import (
    Watched,
    WatchlistError,
    defer,
    load,
    release,
    save,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def _entry(symbol="LITE", eligible_on="", hours_ago=1.0, trigger="t1") -> Watched:
    return Watched(
        symbol=symbol, reason="reports earnings Tue 08/11",
        entry_price=100.0, stop_price=98.0, target_price=110.0,
        trigger_at=trigger,
        added_at=(NOW - timedelta(hours=hours_ago)).isoformat(),
        eligible_on=eligible_on,
    )


# ---- eligibility --------------------------------------------------------

def test_an_entry_with_no_date_retries_next_run():
    """Position limit and buying power clear on their own — nothing to wait
    for beyond the next scan."""
    assert _entry().eligible(date(2026, 8, 6))


def test_an_entry_waits_until_its_date():
    """Earnings on the 11th: not before, eligible on and after."""
    e = _entry(eligible_on="2026-08-11")
    assert not e.eligible(date(2026, 8, 10))
    assert e.eligible(date(2026, 8, 11))
    assert e.eligible(date(2026, 8, 12))


def test_a_malformed_date_does_not_trap_an_entry_forever():
    assert _entry(eligible_on="not-a-date").eligible(date(2026, 8, 6))


# ---- releasing ----------------------------------------------------------

def test_ready_and_waiting_are_separated():
    entries = (_entry("A"), _entry("B", eligible_on="2026-08-20"))
    ready, waiting = release(entries, today=date(2026, 8, 6), now=NOW)
    assert [e.symbol for e in ready] == ["A"]
    assert [e.symbol for e in waiting] == ["B"]


def test_a_stale_entry_is_dropped_not_revived():
    """Its stop describes a swing low the market may have taken out days ago.
    Reviving it would trade a setup nobody has re-detected."""
    old = _entry(hours_ago=100.0)
    ready, waiting = release((old,), today=date(2026, 8, 6), now=NOW,
                             max_age_hours=48.0)
    assert ready == () and waiting == ()


def test_a_fresh_entry_survives():
    ready, _ = release((_entry(hours_ago=4.0),), today=date(2026, 8, 6), now=NOW)
    assert len(ready) == 1


# ---- deferring ----------------------------------------------------------

def test_rescanning_the_same_setup_updates_rather_than_duplicates():
    entries = defer((), _entry())
    entries = defer(entries, _entry())
    assert len(entries) == 1


def test_a_different_trigger_bar_is_a_different_setup():
    entries = defer(defer((), _entry(trigger="t1")), _entry(trigger="t2"))
    assert len(entries) == 2


# ---- persistence --------------------------------------------------------

def test_a_round_trip_preserves_the_levels(tmp_path):
    path = tmp_path / "w.json"
    save((_entry(),), path)
    restored = load(path)
    assert restored[0].stop_price == pytest.approx(98.0)
    assert restored[0].reason.startswith("reports earnings")


def test_no_store_yet_is_not_an_error(tmp_path):
    assert load(tmp_path / "missing.json") == ()


def test_a_corrupt_store_is_reported_not_silently_emptied(tmp_path):
    path = tmp_path / "w.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(WatchlistError):
        load(path)


def test_writes_leave_no_partial_file(tmp_path):
    path = tmp_path / "w.json"
    save((_entry(),), path)
    assert path.exists() and not path.with_suffix(".tmp").exists()
