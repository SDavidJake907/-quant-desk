from datetime import datetime, timedelta

from scanner.ops import peak_sell as ps


def _r(pl, **vals):
    base = dict.fromkeys(ps.CHECKS)
    base.update(vals)
    return ps.Readings(symbol="X", pl_pct=pl, values=base)


HOT = {"trend_5m": 1.0, "vwap_5m": 0.5, "crsi_1h": 80, "stc_1h": 99, "stc_1d": 95, "pctb_1d": 0.9}


def test_all_six_in_profit_is_eligible():
    r = _r(2.0, **HOT)
    assert r.score() == 6 and r.may_sell()


def test_red_position_never_eligible():
    assert not _r(-0.5, **HOT).may_sell()


def test_needs_four_of_six():
    three = dict(HOT, stc_1d=10, pctb_1d=0.2, crsi_1h=20)
    four = dict(HOT, stc_1d=10, pctb_1d=0.2)
    assert not _r(1.0, **three).may_sell()
    assert _r(1.0, **four).may_sell()


def test_missing_reading_counts_as_not_met():
    r = _r(1.0, trend_5m=1.0, vwap_5m=0.5, crsi_1h=80)
    assert r.met()["stc_1h"] is None and r.score() == 3 and not r.may_sell()


def test_thresholds_are_inclusive():
    r = _r(1.0, trend_5m=1.0, vwap_5m=0.20, crsi_1h=63, stc_1h=85, stc_1d=80, pctb_1d=0.80)
    assert r.score() == 6


def test_approved_needs_her_sell_and_eligibility():
    ok = ps.Readings("A", 1.0, HOT)
    red = ps.Readings("B", -1.0, HOT)
    ans = {"positions": [{"symbol": "A", "action": "sell", "why": "w"},
                         {"symbol": "B", "action": "sell", "why": "w"},
                         {"symbol": "C", "action": "sell", "why": "w"}]}
    assert ps.approved(ans, [ok, red]) == [("A", "w")]
    assert ps.approved({"positions": [{"symbol": "A", "action": "hold", "why": ""}]}, [ok]) == []
    assert ps.approved(None, [ok]) == []


def test_hourly_aligns_to_the_open():
    t0 = datetime(2026, 10, 21, 9, 30)
    five = [(t0 + timedelta(minutes=5 * i), 1, 2, 0.5, 1 + i, 10) for i in range(14)]
    h = ps.hourly(five)
    assert len(h) == 2 and h[0][4] == 12 and h[1][0] == datetime(2026, 10, 21, 10, 30)


def test_readings_computes_every_check_with_enough_data():
    t0 = datetime(2026, 9, 1, 9, 30)
    five = []
    for d in range(30):
        for i in range(78):
            p = 100 + d + i * 0.01
            five.append((t0 + timedelta(days=d, minutes=5 * i), p, p + 0.1, p - 0.1, p, 1000))
    r = ps.readings("X", 1.0, five, [100 + i * 0.5 for i in range(200)])
    assert all(v is not None for v in r.values.values())


def test_prompt_contains_plan_and_readings():
    text = ps.prompt([ps.Readings("A", 1.5, HOT)], market="up")
    assert "[A]" in text and "6 of 6" in text and "ELIGIBLE" in text and "PLAN" in text
