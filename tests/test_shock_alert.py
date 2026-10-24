"""Crash and shock alerts (ops/shock_alert.py), checked against Thursday 10-22."""

from datetime import date

from scanner.ops import shock_alert as sa

THEME = ["MU", "NVDA"]


def kinds(**kw):
    base = dict(spy=None, qqq=None, smh=None, vix=None, up_share=None, theme_held=THEME, reporting=(), regular=False)
    return [a.kind for a in sa.classify(**{**base, **kw})]


def test_10_22_premarket_would_have_warned():
    # 04:00 ET on 10-22: SPY -1.0%, QQQ -1.7%, SMH -3.1%, VIX ~17
    assert kinds(spy=-1.0, qqq=-1.7, smh=-3.1, vix=16.7) == ["theme_shock"]


def test_10_22_close_reads_as_rotation_not_crash():
    out = kinds(spy=-1.47, qqq=-2.9, smh=-6.0, vix=19.0, up_share=338 / 495, regular=True)
    assert "rotation" in out and "crash" not in out and "theme_shock" in out


def test_night_before_needs_two_theme_names_and_a_report():
    assert kinds(reporting=("META",)) == ["bellwether"]
    assert kinds(reporting=("META",), theme_held=["MU"]) == []
    assert kinds(reporting=()) == []


def test_stress_and_crash():
    assert kinds(spy=-1.8, theme_held=[]) == ["stress"]
    assert kinds(vix=26.0, theme_held=[]) == ["stress"]
    assert kinds(spy=-3.5, up_share=0.1, theme_held=[]) == ["crash"]
    assert kinds(vix=31.0, theme_held=[]) == ["crash"]                    # SPY unknown: still formats
    assert kinds(spy=-0.4, qqq=-0.5, smh=-0.8, vix=14.0) == []


def test_active_and_block():
    rows = [{"kind": "theme_shock", "text": "THEME SHOCK", "until": "2026-10-26"},
            {"kind": "stress", "text": "old", "until": "2026-10-20"}]
    live = sa.active(rows, date(2026, 10, 26))
    assert [a.kind for a in live] == ["theme_shock"]
    text = sa.block(live)
    assert "nothing is sold by them" in text and "3-day minimum hold" in text
    assert sa.block([]) == ""
    assert sa.theme_holdings(["MU", "XOM"], {"MU": "Information Technology", "XOM": "Energy"}) == ["MU"]


def test_hold_override_only_for_theme_names_under_an_override_alert():
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("gemma_trade_for_alerts", "scripts/gemma_trade.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gemma_trade_for_alerts"] = mod
    spec.loader.exec_module(mod)
    sectors = {"MU": "Information Technology", "XOM": "Energy"}
    shock = [sa.Alert(kind="theme_shock", text="x")]
    assert mod.in_alerted_theme("MU", shock, sectors)
    assert not mod.in_alerted_theme("XOM", shock, sectors)
    assert not mod.in_alerted_theme("MU", [sa.Alert(kind="stress", text="x")], sectors)
