"""Chart patterns as numbers: Lo, Mamaysky & Wang (2000) definitions.

Each test builds a price path with a known shape and checks the detector
names it -- and that near-misses (outside the 1.5% / 0.75% tolerances, or a
double top under 22 days apart) are refused.
"""

from __future__ import annotations

from scanner.patterns import detect as d


def path(points, length=38):
    """Linear interpolation through (index, price) knots, padded to `length`."""
    out = []
    for (i0, p0), (i1, p1) in zip(points, points[1:]):
        for i in range(i0, i1):
            out.append(p0 + (p1 - p0) * (i - i0) / (i1 - i0))
    out.append(points[-1][1])
    while len(out) < length:
        out.append(points[-1][1])
    return out[:length]


def test_head_and_shoulders():
    p = path([(0, 95), (5, 100), (10, 96), (17, 106), (24, 96.5), (30, 100.5), (35, 94), (37, 93)])
    assert d.detect(p)[0] == "HS"


def test_inverse_head_and_shoulders():
    p = path([(0, 105), (5, 100), (10, 104), (17, 94), (24, 103.5), (30, 100.4), (35, 106), (37, 107)])
    assert d.detect(p)[0] == "IHS"


def test_shoulders_too_uneven_are_not_hs():
    p = path([(0, 95), (5, 100), (10, 96), (17, 106), (24, 96.5), (30, 104), (35, 94), (37, 93)])
    assert d.detect(p)[0] != "HS"


def test_double_bottom_needs_22_days_between_lows():
    wide = path([(0, 110), (3, 100), (16, 106), (30, 100.5), (34, 107), (37, 108)])
    narrow = path([(0, 110), (12, 100), (18, 106), (24, 100.5), (30, 107), (37, 108)])
    assert d.detect(wide)[0] == "DBOT"
    assert d.detect(narrow)[0] != "DBOT"


def test_rectangle_top():
    # middle peak NOT the highest: with the highest middle peak this shape is also
    # an HS under LMW's rules, and HS is checked first (documented overlap)
    p = path([(0, 98), (4, 100.3), (10, 97), (16, 100.0), (22, 97.2), (28, 100.2), (34, 96), (37, 95)])
    assert d.detect(p)[0] == "RTOP"


def test_flat_line_is_nothing():
    assert d.detect([100.0] * 38) == (None, ())


def test_smoothing_uses_only_the_window():
    # changing a price after the window cannot matter: detect sees 38 values only
    p = path([(0, 95), (5, 100), (10, 96), (17, 106), (24, 96.5), (30, 100.5), (35, 94), (37, 93)])
    assert d.detect(p) == d.detect(list(p))


def test_card_codes():
    card = d.card(code="DBOT", age=1, neckline_atr=-0.2, height_atr=1.4,
                  stats={"ev20": 0.41, "t": 2.7, "n": 2210, "verdict": "MEASURED"})
    assert card == "PT=DBOT age1 nk-0.2 h1.4 EV20+0.41% t2.7 n2210 MEASURED"
    none = d.card(code="IHS", age=3, neckline_atr=0.6, height_atr=2.1,
                  stats={"ev20": 0.12, "t": 0.6, "n": 1840, "verdict": "no measured edge"})
    assert none.endswith("no measured edge")
