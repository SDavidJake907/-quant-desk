import random
from datetime import date, timedelta

import pytest

from scanner.ops import competition as c
from scanner.ops import factors as fx


def test_ols_recovers_known_loadings_and_alpha():
    rnd = random.Random(1)
    x = [[rnd.gauss(0, 1) for _ in range(4)] for _ in range(400)]
    y = [0.05 + 1.2 * a - 0.5 * b + 0.3 * d + 0.8 * e + rnd.gauss(0, 0.1) for a, b, d, e in x]
    coef, t, r2 = fx.ols(x, y)
    assert [round(v, 1) for v in coef] == [0.1, 1.2, -0.5, 0.3, 0.8] and t[0] > 5 and r2 > 0.95


def test_book_returns_use_yesterdays_money_invested():
    d = [date(2026, 10, 1) + timedelta(days=i) for i in range(3)]
    snaps = [c.Snapshot(day=d[0], book="gemma", pl_total=0.0, invested=1000.0),
             c.Snapshot(day=d[1], book="gemma", pl_total=10.0, invested=2000.0),
             c.Snapshot(day=d[2], book="gemma", pl_total=30.0, invested=2000.0)]
    assert fx.book_returns(snaps, "gemma") == {d[1]: 1.0, d[2]: 1.0}


def test_attribution_refuses_too_few_days():
    with pytest.raises(ValueError, match="trading days"):
        fx.attribute({date(2026, 10, 1): 1.0}, {date(2026, 10, 1): {"MKT": 1, "SMB": 0, "HML": 0, "MOM": 0, "RF": 0}})
