from scanner.advisor import stock_context as sc
from scanner.sources import yahoo_fundamentals as yf

MU = {
    "earningsTrend": {"trend": [
        {"period": "0q", "epsTrend": {"current": {"raw": 38.3}, "90daysAgo": {"raw": 34.9}},
         "epsRevisions": {"upLast30days": {"raw": 26}, "downLast30days": {"raw": 2}}},
        {"period": "0y", "epsTrend": {"current": {"raw": 176.5}, "30daysAgo": {"raw": 156.5},
                                      "90daysAgo": {"raw": 150.9}},
         "epsRevisions": {"upLast30days": {"raw": 28}, "downLast30days": {"raw": 2}}}]},
    "financialData": {"revenueGrowth": {"raw": 3.793}, "earningsGrowth": {"raw": 10.595},
                      "grossMargins": {"raw": 0.807}, "operatingMargins": {"raw": 0.807}},
    "summaryDetail": {"forwardPE": {"raw": 4.37}},
}


def test_parse_uses_this_fiscal_year():
    f = yf.parse("MU", MU)
    assert f.period == "0y" and f.eps_now == 176.5 and f.eps_90d_ago == 150.9
    assert (f.up_30d, f.down_30d) == (28, 2)
    assert f.forward_pe == 4.37


def test_parse_survives_missing_modules():
    f = yf.parse("X", {})
    assert f.eps_now is None and f.up_30d is None
    assert sc.analysts_line(f) == "" and sc.fundamentals_line(f) == ""


def test_analysts_and_fundamentals_lines():
    f = yf.parse("MU", MU)
    a = sc.analysts_line(f)
    assert a == "analysts: EPS est +17.0% (90d), 28 up/2 down (30d)"
    fl = sc.fundamentals_line(f)
    assert fl.startswith("fundamentals: fwd P/E 4.4, rev +379%, earn +1060%")
    assert "gross 81%" in fl and "+81%" not in fl
    assert sc.analysts_line(None) == ""


def test_rvol_and_atr_rank():
    assert sc.rvol([100.0] * 20 + [250.0]) == 2.5
    assert sc.rvol([100.0] * 5) is None
    closes = [100.0 + (i % 5) for i in range(300)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    highs[-1], lows[-1] = closes[-1] + 30, closes[-1] - 30       # a wild last day
    assert sc.atr_rank(highs, lows, closes) == 100.0
    assert sc.atr_rank(highs[:30], lows[:30], closes[:30]) is None


def test_chart_line_is_labelled_context():
    closes = [100.0] * 260
    line = sc.chart_line(110.0, 100.0, 50.0, [1.0] * 260, [101.0] * 260, [99.0] * 260, closes)
    assert line.startswith("chart:")
    assert "vs 50d +10.0%" in line and "vs 200d +120.0%" in line
    assert "RSI" not in line and "MACD" not in line
