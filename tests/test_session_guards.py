"""Two rules about when the market is shut.

ENTRY ACROSS A SESSION BOUNDARY
`fresh_bars` counts BARS, not TIME. Overnight is one bar and seventeen hours,
so a setup that triggered on yesterday's last bar reads as fresh three minutes
after today's open -- which is how EXPE was ordered eighteen hours after its
trigger. Measured: entries across the close are adverse 88% of the time, mean
+0.785%, worst +3.20% against a 1.5% minimum stop.

This is a modelling correction, not an optimisation. The backtest measured
entry at the close of the trigger bar, and across a session boundary that price
was never available -- the market was shut. Same reasoning that adopted the
ex-dividend gate.

EXIT BEFORE THE WEEKEND
A stop is an instruction to an exchange that is closed from Friday afternoon to
Monday morning. This one DOES cost measured return (-0.213R per trade), so it
is a risk decision rather than an edge claim, and it is off unless asked for.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session, is_last_bar_before_weekend

ET = timezone(timedelta(hours=-4))


def _t(day: int, hour: int, minute: int = 0) -> datetime:
    """August 2026: the 10th is a Monday, so day 14 is a Friday."""
    return datetime(2026, 8, day, hour, minute, tzinfo=ET)


class _Bars:
    def __init__(self, times):
        self.times = tuple(times)

    def __len__(self):
        return len(self.times)


class TestCrossesSession:
    def test_two_bars_in_one_session_do_not_cross(self):
        bars = _Bars([_t(11, 15, 0), _t(11, 15, 30)])
        assert crosses_session(bars, 0) is False

    def test_the_last_bar_of_a_day_crosses(self):
        bars = _Bars([_t(11, 15, 30), _t(12, 9, 30)])
        assert crosses_session(bars, 0) is True

    def test_the_final_bar_of_the_series_does_not_crash(self):
        """There is no next bar to compare against. Returning False keeps a
        setup on the newest bar tradeable, which is the whole point of a
        freshness window."""
        bars = _Bars([_t(11, 15, 30)])
        assert crosses_session(bars, 0) is False

    def test_a_weekend_crosses(self):
        bars = _Bars([_t(14, 15, 30), _t(17, 9, 30)])
        assert crosses_session(bars, 0) is True


class TestLastBarBeforeWeekend:
    def test_fridays_final_bar_is_the_boundary(self):
        bars = _Bars([_t(14, 15, 0), _t(14, 15, 30), _t(17, 9, 30)])
        assert is_last_bar_before_weekend(bars, 1) is True
        assert is_last_bar_before_weekend(bars, 0) is False

    def test_a_midweek_close_is_not_the_boundary(self):
        bars = _Bars([_t(11, 15, 30), _t(12, 9, 30)])
        assert is_last_bar_before_weekend(bars, 0) is False

    def test_a_long_weekend_is_detected_without_a_holiday_calendar(self):
        """If Friday is a holiday, Thursday's last bar is the boundary. Reading
        the gap between timestamps gets that right without knowing why the
        market was shut."""
        bars = _Bars([_t(13, 15, 30), _t(17, 9, 30)])
        assert is_last_bar_before_weekend(bars, 0) is True

    def test_the_final_bar_of_the_series_is_not_assumed_to_be_a_boundary(self):
        bars = _Bars([_t(14, 15, 30)])
        assert is_last_bar_before_weekend(bars, 0) is False


class TestPolicy:
    def test_overnight_entries_are_blocked_by_default(self):
        """A correction to a modelling error is on by default, like the
        ex-dividend gate it is modelled on."""
        assert TradingPolicy().block_overnight_entry is True

    def test_the_weekend_flat_rule_is_on(self):
        """Costs -0.213R per trade against a +0.218R measured edge, and was
        turned on anyway: the operator judged an exposure they cannot manage
        worse than the price of removing it. Pinned so the decision cannot be
        lost in a later edit -- the cost is the reason it needs pinning."""
        assert TradingPolicy().flat_before_weekend is True

    def test_the_wind_down_is_friday_only(self):
        """Thursday trades; Friday does not. On arithmetic, not on a weekday
        edge: a Thursday entry has ~17 bars before the flatten against a ~20
        bar mean hold, a Friday entry has ~11. The weekday expectancy
        differences are noise; bars available is not."""
        assert TradingPolicy().wind_down_from_weekday == 4

    def test_each_rule_changes_the_fingerprint(self):
        """Two runs under different rules must never share a fingerprint, or
        the audit log cannot say which rules produced a result."""
        base = TradingPolicy()
        assert base.fingerprint != TradingPolicy(block_overnight_entry=False).fingerprint
        assert base.fingerprint != TradingPolicy(flat_before_weekend=False).fingerprint
        assert base.fingerprint != TradingPolicy(wind_down_from_weekday=3).fingerprint
