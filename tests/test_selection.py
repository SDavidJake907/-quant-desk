"""Selection grading: chosen vs passed-over, and rank correlation of calls."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from scanner.ops import selection as sel

D0 = date(2026, 10, 7)


def bars(opens_closes):
    return [(D0 + timedelta(days=i), o, c) for i, (o, c) in enumerate(opens_closes)]


def test_forward_return_from_open_to_close_and_waits_for_the_horizon():
    b = bars([(100, 101), (101, 103), (103, 110)])
    assert sel.forward_return(b, D0, 3) == pytest.approx(10.0)       # open 100 -> close 110
    assert sel.forward_return(b, D0, 1) == pytest.approx(1.0)
    assert sel.forward_return(b, D0 + timedelta(days=1), 3) is None  # not yet
    assert sel.forward_return(b, D0 - timedelta(days=1), 1) is None  # not a session


def test_spread_chosen_minus_passed_over():
    fwd = {"A": 5.0, "B": 3.0, "C": -1.0, "D": 1.0}
    assert sel.spread(["A", "B"], ["A", "B", "C", "D"], fwd) == pytest.approx(4.0 - 0.0)
    assert sel.spread([], ["A"], fwd) is None
    assert sel.spread(["A", "B", "C", "D"], ["A", "B", "C", "D"], fwd) is None


def test_spearman_handles_ties_and_direction():
    assert sel.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert sel.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    calls, rets = [1, 1, 0, -1, -1], [3.0, 2.0, 0.5, -1.0, -2.0]
    assert sel.spearman(calls, rets) > 0.9
    assert sel.spearman([1, 1], [1, 2]) is None


def test_summary():
    s = sel.summarize([1.0, 2.0, None, -1.0, 2.0])
    assert s.days == 4 and s.positive_days == 3 and s.mean == pytest.approx(1.0)
    assert s.t > 0
    assert sel.summarize([]).days == 0
