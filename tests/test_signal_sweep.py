from datetime import datetime, timedelta

from scanner.signals import sweep

D0 = datetime(2026, 10, 21, 9, 30)
D1 = datetime(2026, 10, 22, 9, 30)


def day(start, n, px=100.0, vol=1000.0):
    t = [start + timedelta(minutes=5 * i) for i in range(n)]
    return t, [px] * n, [px + 1] * n, [px - 1] * n, [px] * n, [vol] * n


def series(last_low, last_close, last_vol, px=100.0):
    t0, o0, h0, l0, c0, v0 = day(D0, 30, px)
    t1, o1, h1, l1, c1, v1 = day(D1, 25, px)
    l1[-1], c1[-1], v1[-1], h1[-1] = last_low, last_close, last_vol, max(px + 1, last_close)
    return t0 + t1, o0 + o1, h0 + h1, l0 + l1, c0 + c1, v0 + v1


def test_detects_a_sweep_that_closes_back_inside_on_volume():
    t, o, h, lo, c, v = series(last_low=97.5, last_close=100.2, last_vol=5000)
    s = sweep.detect(t, o, h, lo, c, v, price=100.2)
    assert s is not None and s.level == 99.0 and s.stop < 97.5 and s.target2 > s.target1 > 100.2


def test_no_sweep_without_close_back_or_volume():
    t, o, h, lo, c, v = series(last_low=97.5, last_close=98.5, last_vol=5000)       # closed below the level
    assert sweep.detect(t, o, h, lo, c, v, price=98.5) is None
    t, o, h, lo, c, v = series(last_low=97.5, last_close=100.2, last_vol=1500)      # no surge
    assert sweep.detect(t, o, h, lo, c, v, price=100.2) is None


def pos():
    return sweep.Open(symbol="X", entry=100.0, entry_at=D1 + timedelta(hours=2), stop=98.0,
                      target1=101.0, target2=103.0, signal_bar=D1 + timedelta(hours=2))


def test_half_at_target1_then_breakeven_or_target2():
    p, sb = pos(), D1 + timedelta(hours=2)
    later = [sb + timedelta(minutes=5), sb + timedelta(minutes=10)]
    now = sb + timedelta(minutes=15)
    p2, out = sweep.manage("5m sweep", p, times=later, highs=[101.5, 101.2], lows=[100.5, 100.4],
                           price=101.0, now=now, scalp=False)
    assert p2.half_done and p2.stop == 100.0 and [x.reason for x in out] == ["target 1 (half)"]
    p3, out = sweep.manage("5m sweep", p2, times=later + [now], highs=[101.5, 101.2, 103.5],
                           lows=[100.5, 100.4, 101.0], price=103.0, now=now + timedelta(minutes=5), scalp=False)
    assert p3 is None and [x.reason for x in out] == ["target 2"]
    p4, out = sweep.manage("5m sweep+scalp", p, times=later, highs=[101.5, 101.2], lows=[100.5, 100.4],
                           price=101.0, now=now, scalp=True)
    assert p4 is None and out[0].reason == "target 1 (all)" and round(out[0].dollars, 2) > 0


def test_stop_and_session_end():
    p, sb = pos(), D1 + timedelta(hours=2)
    _, out = sweep.manage("v", p, times=[sb + timedelta(minutes=5)], highs=[100.5], lows=[97.9],
                          price=98.0, now=sb + timedelta(minutes=10), scalp=False)
    assert out[0].reason == "stop" and out[0].exit == 98.0
    _, out = sweep.manage("v", p, times=[], highs=[], lows=[], price=100.5,
                          now=D1.replace(hour=15, minute=56), scalp=False)
    assert out[0].reason == "session end"
