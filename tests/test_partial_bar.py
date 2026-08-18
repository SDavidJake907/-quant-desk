"""The bar that has not finished yet.

`fetch_intraday` aggregates Yahoo's ticks into buckets and returned all of
them, including the one still forming. Scans run at :03 and :33, three minutes
into a new 30-minute bucket, so the newest "bar" a live scan saw was three
minutes old with 27 minutes left to change.

WHY THAT IS NOT MERELY UNTIDY
The measured strategy enters at the CLOSE OF THE TRIGGER BAR. A partial bar has
no close yet — it has a current price. Entering there is a different trade at a
different price from the one the +0.218R holdout measured, and the difference is
invisible because both are called "the close".

It also lets a setup be acted on before the bar that produced it exists. The
BOS itself is real (price did trade through the level), but the entry price is
provisional, and on the 05:33 AK run — 09:33 ET, three minutes after the open —
the partial bar is the whole of the session so far.

Dropping it costs at most one scan of delay, and `fresh_bars = 2` already
allows for that.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scanner.features.intraday import EXCHANGE_TZ, drop_incomplete

ET = timezone(timedelta(hours=-4))


class _Bars:
    """Minimal stand-in with the fields `drop_incomplete` touches."""

    def __init__(self, times):
        self.times = tuple(times)
        self.opens = self.highs = self.lows = self.closes = self.volumes = \
            tuple(range(len(times)))

    def __len__(self):
        return len(self.times)

    def upto(self, index):
        return _Bars(self.times[:index + 1])


def _bars(*starts):
    return _Bars([datetime(2026, 8, 18, h, m, tzinfo=ET) for h, m in starts])


class TestDropIncomplete:
    def test_a_bar_still_forming_is_dropped(self):
        bars = _bars((9, 30), (10, 0))
        now = datetime(2026, 8, 18, 10, 3, tzinfo=ET)     # 3 min into 10:00
        assert len(drop_incomplete(bars, minutes=30, now=now)) == 1

    def test_a_bar_that_has_closed_is_kept(self):
        bars = _bars((9, 30), (10, 0))
        now = datetime(2026, 8, 18, 10, 31, tzinfo=ET)    # 10:00 bar is done
        assert len(drop_incomplete(bars, minutes=30, now=now)) == 2

    def test_the_opening_bar_three_minutes_in_is_dropped(self):
        """The 05:33 AK run: 09:33 ET, three minutes after the bell. The whole
        session so far is one unfinished bar."""
        bars = _bars((15, 30), (9, 30))
        now = datetime(2026, 8, 18, 9, 33, tzinfo=ET)
        kept = drop_incomplete(bars, minutes=30, now=now)
        assert len(kept) == 1
        assert kept.times[-1].hour == 15

    def test_historical_series_are_untouched(self):
        """Every backtest fetch is of bars long closed; this must be a no-op
        there or it would silently shorten every study."""
        bars = _bars((9, 30), (10, 0), (10, 30))
        now = datetime(2026, 8, 19, 12, 0, tzinfo=ET)     # the next day
        assert len(drop_incomplete(bars, minutes=30, now=now)) == 3

    def test_a_bar_exactly_at_its_boundary_is_complete(self):
        bars = _bars((9, 30), (10, 0))
        now = datetime(2026, 8, 18, 10, 30, tzinfo=ET)
        assert len(drop_incomplete(bars, minutes=30, now=now)) == 2

    def test_a_single_bar_series_is_left_alone(self):
        """Returning an empty series would turn a data quirk into a crash in
        every caller that indexes [-1]."""
        bars = _bars((9, 30))
        now = datetime(2026, 8, 18, 9, 33, tzinfo=ET)
        assert len(drop_incomplete(bars, minutes=30, now=now)) == 1

    def test_it_compares_in_exchange_time_not_local(self):
        """The machine runs in Alaska and the bars are Eastern. Comparing a
        naive local clock against exchange timestamps would drop four hours of
        perfectly good bars."""
        bars = _bars((9, 30), (10, 0))
        now = datetime(2026, 8, 18, 6, 3,
                       tzinfo=timezone(timedelta(hours=-8)))   # 10:03 ET
        assert len(drop_incomplete(bars, minutes=30, now=now)) == 1
