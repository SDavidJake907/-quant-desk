from datetime import date
from types import SimpleNamespace

from scanner.ops import ideas

D = [date(2026, 10, d) for d in (15, 16, 19, 20, 21)]


def test_surprise_prefers_nasdaq_then_computes():
    assert ideas.surprise({"surprise_pct": "12.5"}) == 12.5
    assert ideas.surprise({"surprise_pct": "", "eps": "1.10", "eps_forecast": "1.00"}) == 10.000000000000009
    assert ideas.surprise({"surprise_pct": "", "eps": "", "eps_forecast": "1"}) is None
    assert ideas.surprise({"surprise_pct": "", "eps": "1", "eps_forecast": "0"}) is None


def test_ear_is_close_before_to_day_after_minus_the_index():
    stock = list(zip(D, (100.0, 100.0, 110.0, 112.0, 113.0)))
    bench = list(zip(D, (50.0, 50.0, 50.5, 51.0, 51.0)))
    e, after = ideas.ear(stock, bench, date(2026, 10, 19))
    assert after == date(2026, 10, 20)
    assert round(e, 2) == round((112 / 100 - 51 / 50) * 100, 2)
    assert ideas.ear(stock, bench, date(2026, 10, 21)) is None        # no d+1 yet
    assert ideas.ear(stock, bench, date(2026, 10, 15)) is None        # no d-1


def test_earnings_winners_needs_beat_positive_ear_and_fresh():
    last = date(2026, 10, 20)
    ears = {("A", D[2]): (6.0, last), ("B", D[2]): (-1.0, last),
            ("C", D[2]): (9.0, last), ("E", D[1]): (8.0, D[2])}
    events = [("A", D[2], 8.0), ("B", D[2], 20.0), ("C", D[2], 4.0),
              ("E", D[1], 30.0), ("A", D[2], 8.0)]
    got = ideas.earnings_winners(events, ears, last)
    assert [p.symbol for p in got] == ["A"]        # B ear<0, C surprise<5, E stale, A once


def test_estimates_rising_rule():
    f = lambda now, ago, up, down: SimpleNamespace(eps_now=now, eps_90d_ago=ago, up_30d=up, down_30d=down)
    funds = {"A": f(11.0, 10.0, 3, 0), "B": f(10.2, 10.0, 5, 0), "C": f(12.0, 10.0, 3, 1),
             "D": f(12.0, 10.0, 1, 0), "E": f(13.0, 10.0, 4, 0), "F": f(2.0, -1.0, 4, 0)}
    above = {"A": True, "B": True, "C": True, "D": True, "E": False, "F": True}
    got = ideas.estimates_rising(funds, above)
    assert [p.symbol for p in got] == ["A"]        # B too small, C a cut, D 1 up, E under 50d, F negative base


def test_grade_excess_per_list():
    days = [date(2026, 10, d) for d in (19, 20, 21, 22, 23)]
    bars = {"A": [(d, 10.0, 11.0) for d in days], "B": [(d, 10.0, 10.0) for d in days]}
    bench = [(d, 100.0, 101.0) for d in days]
    recs = [{"day": "2026-10-19", "lists": {"earnings": ["A"], "estimates": ["A", "B"]}}]
    g = ideas.grade(recs, bars, bench, 5)
    assert round(g.excess["earnings"].mean, 2) == 9.0           # +10% vs +1%
    assert round(g.excess["estimates"].mean, 2) == 4.0          # +5% vs +1%
    assert ideas.symbols_needed(recs) == {"A", "B"}
