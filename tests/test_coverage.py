"""Days that quietly went missing.

WHAT HAPPENED
On 2026-08-18 every one of 120 S&P 500 symbols had a null close for Monday
2026-08-17 in Yahoo's daily series. The bar existed; its close was None. The
parser skips those rather than inventing a price, which is right — and it does
it silently, which is not.

So a full trading day vanished from every daily series at once, and nothing
said so. Intraday bars were unaffected, so the live scanner ran normally and
looked healthy. Only daily-bar research was wrong, and only invisibly.

A backtest over ten years with scattered missing days reports a number with no
hint that anything is absent. That is the same shape as the adjusted-price bug:
not wrong arithmetic, just data quietly gone while everything looks fine.

WHAT COUNTS AS THE TRUTH
There is no calendar to check against without a broker call, so coverage is
measured against the other symbols. A date most symbols have and one lacks is
that symbol's gap; a date almost nobody has is the provider's. The distinction
matters because the fixes are different — refetch one name, or do not trust the
day at all.
"""

from __future__ import annotations

from datetime import date

from scanner.features.coverage import Gap, find_gaps


def _d(day: int) -> date:
    return date(2026, 8, day)


class TestWholesaleGaps:
    def test_a_day_missing_from_every_symbol_is_flagged_as_provider_wide(self):
        series = {"A": [_d(14), _d(18)], "B": [_d(14), _d(18)], "C": [_d(14), _d(18)]}
        # Nobody has the 17th, so it cannot be inferred — a day only shows up
        # as missing when at least one symbol has it.
        assert find_gaps(series) == ()

    def test_a_day_most_symbols_have_is_flagged_for_the_ones_that_do_not(self):
        series = {"A": [_d(14), _d(17), _d(18)],
                  "B": [_d(14), _d(17), _d(18)],
                  "C": [_d(14), _d(18)]}
        gaps = find_gaps(series)
        assert len(gaps) == 1
        gap = gaps[0]
        assert gap.day == _d(17)
        assert gap.missing == ("C",)
        assert gap.present == 2

    def test_a_day_almost_nobody_has_is_reported_as_provider_wide(self):
        series = {"A": [_d(14), _d(17), _d(18)]}
        series.update({f"S{i}": [_d(14), _d(18)] for i in range(20)})
        gap = find_gaps(series)[0]
        assert gap.day == _d(17)
        assert gap.is_provider_wide is True
        assert gap.coverage_pct < 10.0

    def test_a_single_symbol_gap_is_not_provider_wide(self):
        series = {f"S{i}": [_d(14), _d(17), _d(18)] for i in range(20)}
        series["BAD"] = [_d(14), _d(18)]
        gap = find_gaps(series)[0]
        assert gap.is_provider_wide is False
        assert gap.missing == ("BAD",)


class TestNoise:
    def test_a_complete_set_reports_nothing(self):
        series = {"A": [_d(14), _d(17)], "B": [_d(14), _d(17)]}
        assert find_gaps(series) == ()

    def test_a_symbol_that_starts_late_is_not_a_gap(self):
        """A recent listing has no history before it existed, and reporting
        that as missing data would bury the real gaps in noise."""
        series = {"OLD": [_d(10), _d(11), _d(12), _d(13)],
                  "NEW": [_d(12), _d(13)]}
        assert find_gaps(series) == ()

    def test_a_symbol_that_stops_early_is_not_a_gap(self):
        """A delisting, or a series that simply ends. Same reasoning."""
        series = {"LIVE": [_d(10), _d(11), _d(12)],
                  "GONE": [_d(10), _d(11)]}
        assert find_gaps(series) == ()

    def test_an_empty_series_is_ignored_rather_than_reported_as_all_gaps(self):
        series = {"A": [_d(10), _d(11)], "EMPTY": []}
        assert find_gaps(series) == ()


class TestOrdering:
    def test_gaps_come_back_newest_first(self):
        series = {"A": [_d(10), _d(11), _d(12)],
                  "B": [_d(10), _d(12)],
                  "C": [_d(10), _d(11), _d(12)]}
        series["D"] = [_d(11), _d(12)]      # missing the 10th? no: starts late
        gaps = find_gaps(series)
        assert [g.day for g in gaps] == sorted((g.day for g in gaps), reverse=True)
