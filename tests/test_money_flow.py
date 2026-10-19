"""money_flow: sector dollar-volume share and the four per-stock flow checks."""

from datetime import date, timedelta

from scanner.features import money_flow as mf
from scanner.features.bars import Bars


def bars(symbol, closes, volumes, spread=1.0):
    days = tuple(date(2026, 1, 1) + timedelta(days=i) for i in range(len(closes)))
    return Bars(symbol=symbol, dates=days, opens=tuple(closes), closes=tuple(closes),
                volumes=tuple(float(v) for v in volumes),
                highs=tuple(c + spread for c in closes), lows=tuple(c - spread for c in closes))


def test_sector_gaining_share_with_positive_returns_is_inflow():
    flat = bars("OLD", [100.0] * 30, [1000] * 30)
    hot = bars("NEW", [100.0] * 25 + [101, 102, 103, 104, 105], [1000] * 25 + [3000] * 5)
    flows = mf.sector_flows({"OLD": flat, "NEW": hot}, {"OLD": "Tech", "NEW": "Energy"})
    assert flows["Energy"].inflow and flows["Energy"].share_change > 0
    assert not flows["Tech"].inflow and flows["Tech"].share_change < 0


def test_share_up_but_prices_down_is_not_inflow():
    flat = bars("OLD", [100.0] * 30, [1000] * 30)
    dump = bars("DUMP", [100.0] * 25 + [99, 98, 97, 96, 95], [1000] * 25 + [3000] * 5)
    flows = mf.sector_flows({"OLD": flat, "DUMP": dump}, {"OLD": "Tech", "DUMP": "Energy"})
    assert flows["Energy"].share_change > 0 and not flows["Energy"].inflow


def test_stock_buying_scores_all_four_points():
    closes = [100.0] * 20 + [100 + i for i in range(1, 11)]
    f = mf.stock_flow("X", bars("X", closes, [1000] * 25 + [2000] * 5), sector_inflow=True)
    assert f.dollar_surge > mf.SURGE
    assert f.up_volume_share == 1.0 and f.obv_rising
    assert f.points == 4 and f.flowing


def test_stock_selling_scores_nothing():
    closes = [100.0] * 20 + [100 - i for i in range(1, 11)]
    f = mf.stock_flow("Y", bars("Y", closes, [1000] * 30), sector_inflow=False)
    assert f.up_volume_share == 0.0 and not f.obv_rising
    assert f.points == 0 and not f.flowing


def test_short_history_is_safe():
    f = mf.stock_flow("Z", bars("Z", [10.0, 11.0], [5, 5]), sector_inflow=True)
    assert f.dollar_surge is None and f.up_volume_share is None
    assert f.points == 1 and not f.flowing
