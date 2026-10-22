from datetime import datetime, timedelta

from scanner.signals import rules

T0 = datetime(2026, 10, 19, 9, 30)          # a Monday


def times(n, step=timedelta(minutes=15)):
    return [T0 + i * step for i in range(n)]


def test_entry_next_open_exit_next_open_after_flip():
    o = [10, 10, 11, 12, 13, 14, 15]
    c = [10, 11, 12, 13, 14, 15, 16]
    h = [x + 0.1 for x in c]
    lo = [x - 0.1 for x in o]
    state = [0, 1, 1, 1, 0, 0, 0]
    t = rules.simulate("X", times(7), o, h, lo, c, state, atr_series=[1.0] * 7, cost=0)
    assert len(t) == 1 and t[0].entry == 11 and t[0].exit == 14 and t[0].reason == "signal"


def test_stop_and_no_reentry_without_a_new_flip():
    o = [10, 10, 10, 9, 5, 5, 5]
    c = [10, 10, 9.5, 8, 5, 5, 5]
    h = [x + 0.1 for x in o]
    lo = [10, 10, 9.4, 6, 4, 4, 4]
    t = rules.simulate("X", times(7), o, h, lo, c, [0, 1, 1, 1, 1, 1, 1], atr_series=[1.0] * 7, cost=0)
    assert len(t) == 1 and t[0].reason == "stop" and t[0].exit == 7.0      # 10 - 3 x 1


def test_friday_close_and_gates():
    fri = [datetime(2026, 10, 23, 15, 0), datetime(2026, 10, 23, 15, 15), datetime(2026, 10, 23, 15, 30),
           datetime(2026, 10, 23, 15, 45), datetime(2026, 10, 26, 9, 30)]
    o = c = h = lo = [10, 10, 10, 10, 10]
    t = rules.simulate("X", fri, o, h, lo, c, [0, 1, 1, 1, 1], atr_series=[1.0] * 5, cost=0)
    assert t[0].reason == "friday close" and t[0].exit_at == fri[3]
    assert rules.simulate("X", fri, o, h, lo, c, [0, 1, 1, 1, 1], atr_series=[1.0] * 5,
                          entry_ok=[False] * 5) == []
    assert rules.simulate("X", fri, o, h, lo, c, [0, 1, 1, 1, 1], atr_series=[1.0] * 5,
                          may_enter=[False] * 5) == []


def test_every_rule_returns_a_state_per_bar():
    import random
    rnd = random.Random(1)
    c = [100.0]
    for _ in range(299):
        c.append(c[-1] * (1 + rnd.gauss(0, 0.01)))
    o = [c[0]] + c[:-1]
    h = [max(a, b) * 1.002 for a, b in zip(o, c)]
    lo = [min(a, b) * 0.998 for a, b in zip(o, c)]
    v = [1000.0] * 300
    for name, (_, rule) in rules.RULES.items():
        s = rule(o, h, lo, c, v)
        assert len(s) == 300 and set(s) <= {0, 1}, name


def test_friday_flat_can_be_switched_off():
    fri = [datetime(2026, 10, 23, 16), datetime(2026, 10, 26, 16), datetime(2026, 10, 27, 16)]
    o = c = h = lo = [10.0, 10.0, 10.0]
    t = rules.simulate("X", fri, o, h, lo, c, [0, 1, 1], atr_series=[1.0] * 3, friday_flat=False)
    assert t == [] or t[0].reason != "friday close"
