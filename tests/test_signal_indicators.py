import math
import random

import pytest

from scanner.signals import indicators as ind


def bars(n=400, seed=7, drift=0.0):
    rnd = random.Random(seed)
    o, h, lo, c, v = [], [], [], [], []
    p = 100.0
    for _ in range(n):
        op = p
        p = max(1.0, p * (1 + drift + rnd.gauss(0, 0.01)))
        o.append(op)
        h.append(max(op, p) * (1 + abs(rnd.gauss(0, 0.004))))
        lo.append(min(op, p) * (1 - abs(rnd.gauss(0, 0.004))))
        c.append(p)
        v.append(1000 + rnd.random() * 1000)
    return o, h, lo, c, v


def test_pine_seeding_for_ema_and_rma():
    assert ind.ema([1, 2, 3, 4], 3) == [None, None, 2.0, 3.0]
    assert ind.rma([1, 2, 3, 4], 2) == [None, 1.5, 2.25, 3.125]
    assert ind.sma([1, 2, 3], 2) == [None, 1.5, 2.5]


def test_rsi_extremes():
    assert ind.rsi(list(range(1, 40)), 14)[-1] == 100.0
    assert ind.rsi(list(range(40, 1, -1)), 14)[-1] == 0.0


ALL = {
    "supertrend": lambda o, h, lo, c, v: ind.supertrend(o, h, lo, c),
    "chandelier": lambda o, h, lo, c, v: ind.chandelier(h, lo, c),
    "halftrend": lambda o, h, lo, c, v: ind.halftrend(h, lo, c),
    "psar": lambda o, h, lo, c, v: ind.psar(h, lo, c=c),
    "nrtr": lambda o, h, lo, c, v: ind.nrtr(c),
    "stc": lambda o, h, lo, c, v: ind.stc(c),
    "connors": lambda o, h, lo, c, v: ind.connors_rsi(c),
    "mfi": lambda o, h, lo, c, v: ind.mfi(h, lo, c, v),
    "pvt": lambda o, h, lo, c, v: ind.pvt(c, v),
    "obv": lambda o, h, lo, c, v: ind.obv(c, v),
    "adx": lambda o, h, lo, c, v: ind.adx(h, lo, c),
    "atr": lambda o, h, lo, c, v: ind.atr(h, lo, c),
}


@pytest.mark.parametrize("name", sorted(ALL))
def test_no_lookahead_prefix_equals_full(name):
    o, h, lo, c, v = bars(300)
    full = ALL[name](o, h, lo, c, v)
    for cut in (120, 200, 299):
        part = ALL[name](o[:cut], h[:cut], lo[:cut], c[:cut], v[:cut])
        assert part == full[:cut], f"{name} changes past values when bars are added (cut {cut})"


def test_trend_followers_go_up_in_a_rally_and_down_in_a_crash():
    o, h, lo, c, v = bars(300, drift=0.004)
    o2, h2, lo2, c2, v2 = bars(150, seed=9, drift=-0.008)
    k = c[-1] / o2[0]
    o, h, lo, c = (o + [x * k for x in o2], h + [x * k for x in h2],
                   lo + [x * k for x in lo2], c + [x * k for x in c2])
    for name in ("supertrend", "chandelier", "halftrend", "nrtr"):
        d = ALL[name](o, h, lo, c, v + v2)
        assert d[280] == 1 and d[-1] == -1, name
    s = ind.psar(h, lo, c=c)
    assert s[280] < c[280] and s[-1] > c[-1]


def test_oscillators_stay_in_range_and_adx_sees_a_trend():
    o, h, lo, c, v = bars(400)
    for name in ("stc", "connors", "mfi"):
        vals = [x for x in ALL[name](o, h, lo, c, v) if x is not None]
        assert vals and all(0 <= x <= 100 for x in vals), name
    _, th, tl, tc, _ = bars(300, drift=0.01)
    assert ind.adx(th, tl, tc)[-1] > 25 > ind.adx(h, lo, c)[-1] - 15


def test_volume_lines():
    assert ind.obv([1, 2, 2, 1], [10, 20, 30, 40]) == [0.0, 20.0, 20.0, -20.0]
    assert ind.pvt([1, 2], [0, 10]) == [0.0, 10.0]
    assert math.isclose(ind.mfi([2] * 16, [1] * 16, [1 + i / 100 for i in range(16)], [1] * 16)[-1], 100.0)
