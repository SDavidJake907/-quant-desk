"""leaders_grade: both lists graded from the report day's open vs the S&P."""

from datetime import date, timedelta

from scanner.ops import leaders_grade as lg

D0 = date(2026, 10, 19)


def series(start_open, closes):
    """(day, open, close) bars from D0; the first open is `start_open`."""
    out, prev = [], start_open
    for i, c in enumerate(closes):
        out.append((D0 + timedelta(days=i), prev if i == 0 else closes[i - 1], c))
    return out


def test_list_return_is_equal_weight_and_needs_every_name():
    bars = {"A": series(100, [101, 102, 110]), "B": series(50, [50, 50, 50])}
    assert round(lg.list_return(["A", "B"], bars, D0, 3), 9) == 5.0   # (+10% + 0%) / 2
    assert lg.list_return(["A", "B"], bars, D0, 4) is None    # not matured
    assert lg.list_return(["A", "MISSING"], bars, D0, 3) is None
    assert lg.list_return([], bars, D0, 3) is None


def test_grade_excess_and_flow_edge():
    bars = {"R": series(100, [100, 100, 102]), "F": series(100, [100, 100, 106])}
    bench = series(100, [100, 100, 101])
    rec = [{"day": D0.isoformat(), "lists": {"rising": ["R"], "flow": ["F"]}}]
    g = lg.grade(rec, bars, bench, 3)
    assert round(g.excess["rising"].mean, 6) == 1.0
    assert round(g.excess["flow"].mean, 6) == 5.0
    assert round(g.flow_edge.mean, 6) == 4.0 and g.flow_edge.positive_days == 1


def test_empty_list_day_is_skipped_not_zero():
    bars = {"R": series(100, [100, 100, 102])}
    bench = series(100, [100, 100, 101])
    rec = [{"day": D0.isoformat(), "lists": {"rising": ["R"], "flow": []}}]
    g = lg.grade(rec, bars, bench, 3)
    assert g.excess["flow"].days == 0 and g.flow_edge.days == 0
    assert g.excess["rising"].days == 1


def test_symbols_needed():
    rec = [{"lists": {"rising": ["A"], "flow": ["B", "A"]}}]
    assert lg.symbols_needed(rec) == {"A", "B"}
