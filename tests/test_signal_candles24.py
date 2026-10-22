from datetime import datetime, timedelta, timezone

from scanner.signals import candles24 as c24

T = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)


def c(minute, px, v=10.0):
    return (T + timedelta(minutes=minute), px, px + 1, px - 1, px, v)


def test_merge_orders_and_dedups():
    m = c24.merge([c(5, 2), c(0, 1)], [c(0, 9), c(10, 3)])
    assert [x[4] for x in m] == [1, 2, 3]


def test_group_into_clock_buckets_and_drop_forming():
    g = c24.group([c(0, 1), c(5, 2), c(10, 3), c(15, 4)], 15)
    assert len(g) == 2 and g[0][1] == 1 and g[0][4] == 3 and g[0][2] == 4 and g[0][5] == 30
    assert len(c24.completed(g, 15, T + timedelta(minutes=20))) == 1
    assert len(c24.completed(g, 15, T + timedelta(minutes=30))) == 2
