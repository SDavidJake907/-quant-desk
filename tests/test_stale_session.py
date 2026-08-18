"""The overnight guard must not depend on a later bar existing.

HOW THE HOLE OPENED
`crosses_session` asks whether the market closed between bar i and bar i+1.
Live, today's still-forming bar supplied that i+1, so a setup on yesterday's
final bar was correctly refused. Then `drop_incomplete` removed the partial
bar — correctly, for its own reasons — and yesterday's final bar became the
LAST bar in the series. No i+1, so the guard returned False, and the setup read
age 0, fresh, and tradeable.

Two fixes, each right alone, reopening between them the exact hole either was
meant to close. The EXPE entry — ordered eighteen hours after its trigger —
would have been possible again.

THE CORRECT QUESTION FOR A LIVE SCAN
Not "is there a later bar in a different session" but "is this bar from an
earlier session than the one being traded". That cannot be answered from the
bar array alone, so the current session is passed in.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from scanner.execution.session import crosses_session, is_stale_session

ET = timezone(timedelta(hours=-4))


class _Bars:
    def __init__(self, times):
        self.times = tuple(times)

    def __len__(self):
        return len(self.times)


def _bars(*stamps):
    return _Bars([datetime(2026, 8, d, h, m, tzinfo=ET) for d, h, m in stamps])


class TestIsStaleSession:
    def test_yesterdays_last_bar_is_stale_even_as_the_final_bar(self):
        """The regression. After drop_incomplete this bar has no successor."""
        bars = _bars((17, 15, 0), (17, 15, 30))
        assert is_stale_session(bars, 1, session=date(2026, 8, 18)) is True

    def test_a_bar_from_the_current_session_is_not_stale(self):
        bars = _bars((18, 9, 30), (18, 10, 0))
        assert is_stale_session(bars, 1, session=date(2026, 8, 18)) is False

    def test_an_older_bar_in_the_current_session_is_not_stale(self):
        """Mid-session setups are the normal case and must stay tradeable."""
        bars = _bars((18, 9, 30), (18, 10, 0), (18, 10, 30))
        assert is_stale_session(bars, 0, session=date(2026, 8, 18)) is False

    def test_a_weekend_gap_is_stale(self):
        bars = _bars((14, 15, 30),)
        assert is_stale_session(bars, 0, session=date(2026, 8, 17)) is True

    def test_an_out_of_range_index_is_not_stale(self):
        assert is_stale_session(_bars((18, 9, 30)), 5, session=date(2026, 8, 18)) is False


class TestBothGuardsTogether:
    """Either predicate alone leaves a gap; the pair does not."""

    def test_the_pair_refuses_yesterdays_final_bar_with_no_successor(self):
        bars = _bars((17, 15, 0), (17, 15, 30))
        today = date(2026, 8, 18)
        assert crosses_session(bars, 1) is False        # the old hole
        assert is_stale_session(bars, 1, session=today) is True
        assert crosses_session(bars, 1) or is_stale_session(bars, 1, session=today)

    def test_the_pair_refuses_a_mid_series_session_boundary(self):
        bars = _bars((17, 15, 30), (18, 9, 30))
        today = date(2026, 8, 18)
        assert crosses_session(bars, 0) is True
        assert is_stale_session(bars, 0, session=today) is True

    def test_the_pair_allows_an_ordinary_intraday_setup(self):
        bars = _bars((18, 9, 30), (18, 10, 0), (18, 10, 30))
        today = date(2026, 8, 18)
        for index in range(3):
            assert not (crosses_session(bars, index)
                        or is_stale_session(bars, index, session=today)), index
