"""Accumulated-history tests.

This store is meant to outlive the provider's 86-day window, which means a bad
write is permanent and invisible. The partial-bar test is the one that matters:
a bar captured mid-formation has a real high and a real low, both wrong, and it
will manufacture swing pivots that never happened — months later, in a backtest
nobody will think to doubt.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from scanner.features import history
from scanner.features.history import (
    HistoryError,
    collect,
    is_complete,
    merge,
    trim,
    to_bars,
)
from scanner.features.intraday import EXCHANGE_TZ, IntradayBars

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=EXCHANGE_TZ)


def _bars(start_hour: float, count: int, minutes: int = 30, base: float = 100.0):
    start = datetime(2026, 8, 5, 9, 30, tzinfo=EXCHANGE_TZ) + timedelta(
        minutes=int(start_hour * 60)
    )
    times = tuple(start + timedelta(minutes=minutes * i) for i in range(count))
    return IntradayBars(
        symbol="TEST", interval_minutes=minutes, alignment="session",
        times=times,
        opens=tuple(base + i for i in range(count)),
        highs=tuple(base + i + 1 for i in range(count)),
        lows=tuple(base + i - 1 for i in range(count)),
        closes=tuple(base + i + 0.5 for i in range(count)),
        volumes=tuple(1000.0 for _ in range(count)),
    )


@pytest.fixture(autouse=True)
def store_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "STORE_DIR", tmp_path)
    return tmp_path


# ---- the partial bar ---------------------------------------------------

def test_a_bar_still_forming_is_not_stored():
    """At 12:00 the 12:00-12:30 bar holds half its trade. Its high and low
    are real numbers and both are wrong."""
    assert not is_complete(datetime(2026, 8, 5, 12, 0, tzinfo=EXCHANGE_TZ), 30, NOW)
    assert is_complete(datetime(2026, 8, 5, 11, 30, tzinfo=EXCHANGE_TZ), 30, NOW)


def test_merge_drops_the_incomplete_tail():
    bars = _bars(0, 6)  # 09:30 .. 12:00, so the last one is still forming
    rows = merge([], bars, now=NOW)
    stored = [datetime.fromtimestamp(r[0], tz=EXCHANGE_TZ) for r in rows]
    assert datetime(2026, 8, 5, 12, 0, tzinfo=EXCHANGE_TZ) not in stored
    assert len(rows) == 5


def test_a_partial_bar_is_corrected_once_it_completes():
    """The safety net for a bar that slipped in early, or a provider revision."""
    stale = _bars(0, 3)
    rows = merge([], stale, now=NOW)
    revised = _bars(0, 3, base=500.0)
    rows = merge(rows, revised, now=NOW)
    assert len(rows) == 3
    assert rows[0][4] == pytest.approx(500.5)   # newer read wins


# ---- accumulating past the window --------------------------------------

def test_older_bars_survive_a_fetch_that_no_longer_covers_them():
    """The whole point: the provider's window rolls forward and the store
    does not roll with it."""
    old = merge([], _bars(0, 3), now=NOW)
    newer = _bars(2, 3)              # a later fetch, no overlap with the first
    rows = merge(old, newer, now=NOW)
    assert len(rows) > len(old)
    assert rows[0][0] == old[0][0]   # the oldest bar is still there


def test_merge_keeps_rows_in_order():
    rows = merge(merge([], _bars(2, 3), now=NOW), _bars(0, 3), now=NOW)
    assert [r[0] for r in rows] == sorted(r[0] for r in rows)


def test_collection_is_idempotent():
    """Running the collector five times a day must equal running it once."""
    once = collect(_bars(0, 4), now=NOW)
    for _ in range(4):
        again = collect(_bars(0, 4), now=NOW)
    assert once == again


def test_retention_keeps_newest_rows_only():
    rows = merge([], _bars(0, 6), now=NOW)
    kept = trim(rows, max_bars=3)
    assert len(kept) == 3
    assert kept == rows[-3:]


def test_collection_applies_interval_retention_cap(monkeypatch):
    monkeypatch.setitem(history.MAX_STORED_BARS, 30, 3)
    collect(_bars(0, 4), now=NOW)
    assert len(history.load("TEST", 30, "session")) == 3


# ---- round trip --------------------------------------------------------

def test_stored_bars_rebuild_identically():
    original = _bars(0, 4)
    collect(original, now=NOW)
    restored = history.accumulated("TEST", 30, "session")
    assert restored.closes == original.closes[: len(restored)]
    assert restored.times[0] == original.times[0]
    assert restored.interval_minutes == 30


def test_nothing_collected_yet_is_not_an_error():
    assert history.accumulated("NEVER", 30, "session") is None


def test_alignment_is_part_of_the_key():
    """Session- and continuous-aligned bars are different series; merging
    them would produce structure that exists in neither."""
    a = history.store_path("MU", 30, "session")
    b = history.store_path("MU", 30, "continuous")
    assert a != b


def test_interval_is_part_of_the_key():
    assert history.store_path("MU", 30, "session") != history.store_path("MU", 240, "session")


# ---- failure modes -----------------------------------------------------

def test_a_corrupt_store_is_reported_not_silently_emptied():
    """Returning [] here would quietly discard months of collection and
    start over, and nothing would look wrong."""
    path = history.store_path("TEST", 30, "session")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(HistoryError):
        history.load("TEST", 30, "session")


def test_a_series_of_one_bar_is_not_a_series():
    with pytest.raises(HistoryError):
        to_bars("TEST", 30, "session", [[1.0, 1, 1, 1, 1, 1]])


def test_writes_do_not_leave_a_half_written_store():
    collect(_bars(0, 4), now=NOW)
    path = history.store_path("TEST", 30, "session")
    assert path.exists()
    assert not path.with_suffix(".tmp").exists()
