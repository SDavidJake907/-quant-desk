"""The flatten runs before EVERY weekend-length closure, not only on Fridays.

Both triggers were Friday-only. When Friday is a market holiday the last
session is Thursday and nothing flattened -- positions rode a four-day close.
FREEZE.md lists them: 2026-12-24, 2026-12-31, 2027-03-25, 2027-06-17.
"""

from datetime import date

import pytest

from scanner.execution.flatten import closes_for_weekend, flatten_alert_text


@pytest.mark.parametrize("today,next_session,expected", [
    (date(2026, 10, 9), date(2026, 10, 12), True),     # an ordinary Friday
    (date(2026, 10, 8), date(2026, 10, 9), False),     # an ordinary Thursday
    (date(2026, 12, 24), date(2026, 12, 28), True),    # Thu before Christmas Friday
    (date(2026, 12, 31), date(2027, 1, 4), True),      # Thu before New Year's Friday
    (date(2027, 3, 25), date(2027, 3, 29), True),      # Thu before Good Friday
    (date(2027, 6, 17), date(2027, 6, 21), True),      # Thu before Juneteenth (obs.)
    (date(2026, 11, 25), date(2026, 11, 27), False),   # Wed before Thanksgiving: one day shut
    (date(2026, 11, 27), date(2026, 11, 30), True),    # Thanksgiving Friday, early close
])
def test_last_session_before_a_closure_of_three_days_or_more(today, next_session, expected):
    assert closes_for_weekend(today, next_session) is expected


def test_no_known_next_session_errs_toward_flattening():
    # A calendar read that fails must not leave positions over a weekend.
    assert closes_for_weekend(date(2026, 10, 9), None) is True


def test_the_alert_names_what_is_still_open():
    text = flatten_alert_text(["INTCx169", "SLBx409"], working_orders=True,
                              when="2026-10-09 11:52 AKDT")
    assert "INTCx169" in text and "SLBx409" in text
    assert "close" in text.lower()
