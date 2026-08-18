"""Fill quality, in units that mean something.

THE BUG THIS FIXES IS IN THE REPORTING, NOT THE TRADING
`positions.py` reported "mean stop fill +1.4955 vs the stop level" — dollars
per share, with no risk-per-share beside it. That number was quoted repeatedly
as evidence that paper fills were optimistic, and it could not support the
claim: $1.50 on AMD's $8.51 stop distance is a very different thing from $1.50
on CCL's $0.61.

Converted properly, AMD's stop filled +0.354R ABOVE its level and CCL's
-0.033R below. Only in R can the two be averaged, and only in R can a fill be
compared with the expectancy it erodes.

SIGN CONVENTION: POSITIVE IS ALWAYS A COST
A buy filling higher than intended and a sell filling lower than intended are
both adverse, and they have opposite arithmetic. Folding the side into the
sign means a mean over mixed records is meaningful, which a raw price
difference never is.
"""

from __future__ import annotations

import pytest

from scanner.execution.slippage import FillRecord, summarise


def _entry(intended, actual, risk=1.0):
    return FillRecord(symbol="X", kind="entry", side="buy", intended=intended,
                      actual=actual, risk_per_share=risk)


def _stop(intended, actual, risk=1.0):
    return FillRecord(symbol="X", kind="stop", side="sell", intended=intended,
                      actual=actual, risk_per_share=risk)


class TestSignConvention:
    def test_paying_up_on_a_buy_is_a_positive_cost(self):
        assert _entry(100.0, 100.5, risk=1.0).adverse_r == pytest.approx(0.5)

    def test_getting_filled_better_on_a_buy_is_negative(self):
        assert _entry(100.0, 99.5, risk=1.0).adverse_r == pytest.approx(-0.5)

    def test_a_sell_filling_below_its_level_is_a_positive_cost(self):
        """The real behaviour of a stop: it becomes a market order and fills at
        or worse than the level."""
        assert _stop(90.0, 89.0, risk=2.0).adverse_r == pytest.approx(0.5)

    def test_a_sell_filling_above_its_level_is_negative(self):
        """Only paper does this. Recording it as a negative cost is what makes
        the optimism visible in the mean."""
        assert _stop(90.0, 93.01, risk=8.51).adverse_r == pytest.approx(-0.354, abs=1e-3)

    def test_both_sides_average_meaningfully(self):
        """The whole point of folding side into the sign."""
        records = (_entry(100.0, 100.5, 1.0), _stop(90.0, 89.5, 1.0))
        assert summarise(records).mean_adverse_r == pytest.approx(0.5)


class TestNormalisation:
    def test_the_same_dollar_gap_differs_by_risk(self):
        """$1.50 on an $8.51 stop and on a $0.61 stop are not comparable, which
        is exactly what the old report averaged."""
        wide = _stop(100.0, 98.5, risk=8.51)
        tight = _stop(100.0, 98.5, risk=0.61)
        assert wide.adverse_r < tight.adverse_r
        assert tight.adverse_r > 2.0

    def test_a_non_positive_risk_yields_no_r(self):
        """No stop distance means no denominator. Reporting zero would look
        like a perfect fill."""
        assert _stop(100.0, 99.0, risk=0.0).adverse_r is None


class TestClassification:
    def test_a_stop_filling_worse_than_its_level_is_flagged_adverse(self):
        assert _stop(90.0, 89.0, 2.0).is_adverse is True

    def test_a_stop_filling_better_than_its_level_is_flagged_favourable(self):
        """A real stop cannot do this. A run where these dominate is being
        flattered and the report must say so."""
        assert _stop(90.0, 91.0, 2.0).is_favourable is True

    def test_an_exact_fill_is_neither(self):
        record = _stop(90.0, 90.0, 2.0)
        assert record.is_adverse is False and record.is_favourable is False


class TestSummary:
    def test_an_empty_set_reports_nothing_rather_than_zero(self):
        """Zero slippage and no data are different claims."""
        summary = summarise(())
        assert summary.count == 0
        assert summary.mean_adverse_r is None

    def test_the_worst_case_is_kept_separately_from_the_mean(self):
        """The loss tail is what matters; subtracting only the average
        understates exactly the trades that hurt."""
        records = (_stop(90.0, 89.9, 1.0), _stop(90.0, 86.0, 1.0))
        summary = summarise(records)
        assert summary.worst_adverse_r == pytest.approx(4.0)
        assert summary.mean_adverse_r == pytest.approx(2.05)

    def test_favourable_fills_are_counted(self):
        records = (_stop(90.0, 91.0, 1.0), _stop(90.0, 89.0, 1.0))
        assert summarise(records).favourable == 1

    def test_a_sample_this_small_is_marked_as_such(self):
        """Two stopped trades cannot support a distribution, and a report that
        does not say so invites exactly the over-reading this module exists to
        prevent."""
        assert summarise((_stop(90.0, 89.0, 1.0),) * 2).is_reportable is False
        assert summarise((_stop(90.0, 89.0, 1.0),) * 30).is_reportable is True
