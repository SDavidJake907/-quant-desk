"""The penny watch-list shadow (operator 2026-10-27: "keep it, running as a shadow.. no trades").
Records what a $1-$5 book would buy each day; graded against IWM; never orders."""

from scanner.ops import penny_shadow as ps


def test_only_listed_common_stocks_with_plain_symbols():
    ok = {"symbol": "ABCD", "exchange": "NASDAQ", "tradable": True, "status": "active", "name": "Abcd Inc."}
    assert ps.is_common_stock(ok)
    assert not ps.is_common_stock({**ok, "exchange": "OTC"})
    assert not ps.is_common_stock({**ok, "symbol": "ABCD.W"})
    assert not ps.is_common_stock({**ok, "tradable": False})
    assert not ps.is_common_stock({**ok, "name": "Something Bitcoin ETF"})
    assert not ps.is_common_stock({**ok, "name": "Abcd Acquisition Corp Warrant"})


def test_band_is_one_to_five_dollars():
    assert ps.in_band(1.0) and ps.in_band(4.99)
    assert not ps.in_band(0.99) and not ps.in_band(5.01) and not ps.in_band(None)


def test_passes_needs_volume_trend_and_profit():
    closes = [2.0] * 150 + [3.0] * 30                    # up over 6 months, above the 50-day
    vols = [3_000_000] * 180                              # ~$9M a day at $3
    assert ps.passes(closes, vols, operating_margin=0.05, reverse_split=False) is None
    assert "traded" in ps.passes(closes, [100_000] * 180, operating_margin=0.05, reverse_split=False)
    assert "operating margin" in ps.passes(closes, vols, operating_margin=-0.2, reverse_split=False)
    assert "operating margin" in ps.passes(closes, vols, operating_margin=None, reverse_split=False)
    assert "reverse split" in ps.passes(closes, vols, operating_margin=0.05, reverse_split=True)
    falling = [3.0] * 150 + [2.0] * 30
    assert ps.passes(falling, vols, operating_margin=0.05, reverse_split=False) is not None


def test_top_picks_strongest_momentum_first():
    rows = {"AAA": 40.0, "BBB": 120.0, "CCC": None, "DDD": 90.0, "EEE": 10.0, "FFF": -5.0}
    assert ps.top(rows, n=3) == ["BBB", "DDD", "AAA"]
    assert ps.top({"FFF": -5.0, "GGG": 0.0}) == []          # negative momentum is never a pick


def test_grade_is_the_picks_average_minus_iwm():
    record = {"day": "2026-10-27", "picks": [{"symbol": "AAA", "close": 2.0}, {"symbol": "BBB", "close": 4.0}],
              "iwm_close": 200.0}
    later = {"AAA": 2.2, "BBB": 3.8, "IWM": 202.0}          # +10%, -5%, IWM +1%
    assert ps.excess(record, later) == 1.5                 # (10 - 5) / 2 - 1
    assert ps.excess(record, {"AAA": 2.2, "IWM": 202.0}) is None   # a pick without a price: ungraded
    assert ps.excess({**record, "picks": []}, later) is None
