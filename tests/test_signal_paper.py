from datetime import datetime, timedelta

from scanner.signals import paper

T = [datetime(2026, 10, 22, 10, 0) + timedelta(minutes=15 * i) for i in range(6)]
NOW = T[-1] + timedelta(minutes=16)


def run(state, *, position=None, last_seen=None, lows=None, gate=True, friday=False, price=100.0):
    return paper.step("15m x", "AAA", times=T[:len(state)], lows=lows or [99.0] * len(state), state=state,
                      gate_ok=gate, atr_now=1.0, price=price, now=NOW, position=position,
                      last_seen=last_seen, friday_flat=friday)


def test_enters_on_a_fresh_flip_once():
    pos, closed, seen = run([0, 0, 1])
    assert pos is not None and pos.entry == 100 and pos.stop == 97.0 and closed is None
    assert run([0, 0, 1], last_seen=seen)[0] is None          # same candle: not again
    assert run([0, 1, 1])[0] is None                           # no flip on the newest candle
    assert run([0, 0, 1], gate=False)[0] is None               # ADX gate
    assert run([0, 0, 1], friday=True)[0] is None              # no entries into the weekend


def test_exits_on_signal_stop_and_friday():
    pos, _, seen = run([0, 0, 1])
    p2, closed, _ = run([0, 0, 1, 0], position=pos, last_seen=seen, price=102.0)
    assert p2 is None and closed.reason == "signal" and round(closed.net_pct, 2) == round((102 * 0.9995 / (100 * 1.0005) - 1) * 100, 2)
    _, closed, _ = run([0, 0, 1, 1], position=pos, last_seen=seen, lows=[99, 99, 99, 96.5], price=98.0)
    assert closed.reason == "stop" and closed.exit == 97.0
    _, closed, _ = run([0, 0, 1, 1], position=pos, last_seen=seen, friday=True, price=101.0)
    assert closed.reason == "friday close"
    p5, closed, _ = run([0, 0, 1, 1], position=pos, last_seen=seen)
    assert p5 == pos and closed is None


def test_leaderboard_orders_by_dollars():
    pos, _, _ = run([0, 0, 1])
    a = paper._close("A", pos, 110.0, NOW, "signal")
    b = paper._close("B", pos, 90.0, NOW, "stop")
    board = paper.leaderboard([b, a, a])
    assert [s.version for s in board] == ["A", "B"] and board[0].trades == 2 and board[0].wins == 2
