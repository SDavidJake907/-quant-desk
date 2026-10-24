from datetime import datetime

from scanner.ops import market_monitor as mm

T0 = datetime(2026, 10, 22, 10, 0)


def h(sym, chg=0.0, price=100.0, stop=None, atr=None, entered=None):
    return mm.Holding(symbol=sym, day_change_pct=chg, price=price, stop=stop, atr_pct=atr, entered_at=entered)


def test_market_alarm_at_the_line():
    assert mm.market_alarm({"SPY": -1.16, "QQQ": -2.35}) == "QQQ -2.35%"
    assert mm.market_alarm({"SPY": -1.49, "QQQ": -1.0}) is None
    assert mm.market_alarm({"SPY": None, "QQQ": -1.5}) == "QQQ -1.50%"


def test_position_alarms():
    assert mm.position_alarm(h("A", chg=-3.2)) == "A -3.2% today"
    assert "above its stop" in mm.position_alarm(h("B", price=100, stop=98.0, atr=2.7))
    assert mm.position_alarm(h("C", chg=-1.0, price=100, stop=90.0, atr=2.0)) is None


def test_market_alarm_sells_only_what_was_held_before_it_started():
    old, new = h("OLD", entered=datetime(2026, 10, 21, 10)), h("NEW", entered=datetime(2026, 10, 22, 11))
    out = mm.picks_to_sell([old, new], alarm_started=T0, market_reason="QQQ -2.35%", concerns={})
    assert out == [("OLD", "market alarm: QQQ -2.35%")]
    out = mm.picks_to_sell([new], alarm_started=T0, market_reason="QQQ -2.35%", concerns={"NEW": "near stop"})
    assert out == [("NEW", "Gemma concern: near stop")]
    assert mm.picks_to_sell([old], alarm_started=None, market_reason=None, concerns={}) == []


def test_sell_if_needs_yes_her_own_sell_if_and_a_real_quote_for_each_part():
    facts = {"MU": "chart: vs 50d -9.3% | volume 2.4x its 20-day average", "VLO": "refining margins steady",
             "DDOG": "Bernstein retracts its upgrade"}
    sell_ifs = {"MU": "MU falls below its 50-day average on high volume.",
                "VLO": "margins fall", "NUE": "x",
                "DDOG": "DDOG falls below its 50-day average or the Bernstein upgrade is retracted."}
    half = {"holdings": [{"symbol": "MU", "met": "yes", "quotes": ["vs 50d -9.3%"]}]}
    assert mm.sell_if_sells(half, sell_ifs, facts) == []                          # the 10-22 MU case
    both = {"holdings": [{"symbol": "MU", "met": "yes", "quotes": ["vs 50d -9.3%", "volume 2.4x its 20-day average"]}]}
    assert mm.sell_if_sells(both, sell_ifs, facts) == [("MU", "vs 50d -9.3% | volume 2.4x its 20-day average")]
    either = {"holdings": [{"symbol": "DDOG", "met": "yes", "quotes": ["Bernstein retracts its upgrade"]}]}
    assert mm.sell_if_sells(either, sell_ifs, facts)[0][0] == "DDOG"              # 'or': one part is enough
    invented = {"holdings": [{"symbol": "VLO", "met": "yes", "quotes": ["margins collapsed"]}]}
    assert mm.sell_if_sells(invented, sell_ifs, facts) == []
    assert mm.sell_if_sells(None, {}, {}) == []
    assert mm.parts_needed("below 50d on high volume") == 2 and mm.parts_needed("a or b and c") == 1


def test_alerts_reach_gemmas_prompt():
    rows = [("MU", "below its 50-day average on high volume", -1.0, -5.9)]
    with_alert = mm.sell_if_prompt({"MU": "facts"}, rows, market="weak", alerts=["QQQ -2.65% today", "MU -5.9% today"])
    assert "ALERTS from the desk monitor" in with_alert and "MU -5.9% today" in with_alert
    assert "ALERTS" not in mm.sell_if_prompt({"MU": "facts"}, rows, market="weak")


# --- 2026-10-23: sell-ifs already true at the buy ------------------------------

def test_bought_today_uses_the_et_calendar_date():
    from datetime import datetime, timezone
    buy = datetime(2026, 10, 23, 21, 17, tzinfo=timezone.utc)          # 5:17 PM ET Friday
    assert mm.bought_today(buy, datetime(2026, 10, 23, 23, 45, tzinfo=timezone.utc))
    assert not mm.bought_today(buy, datetime(2026, 10, 26, 13, 45, tzinfo=timezone.utc))
    assert not mm.bought_today(None, datetime(2026, 10, 23, 23, 45, tzinfo=timezone.utc))


def test_hourly_prompt_shows_the_buy_and_the_after_the_buy_rule():
    rows = [("SNDK", "Price falls below its 50-day average.", -0.5, 0.2,
             "bought Fri 10-23 19:16 ET at 1,232.00")]
    text = mm.sell_if_prompt({"SNDK": "vs 50d -22.3%"}, rows, market="ok")
    assert "bought Fri 10-23 19:16 ET at 1,232.00" in text
    assert "AFTER you bought" in text and "every hour" in text


def test_before_buy_prompt_asks_if_already_true():
    rows = [("SNDK", "Price falls below its 50-day average.", 0.0, None)]
    text = mm.sell_if_prompt({"SNDK": "vs 50d -22.3%"}, rows, market="ok", before_buy=True)
    assert "about to BUY" in text and "ALREADY TRUE" in text and "P/L" not in text
    answer = {"holdings": [{"symbol": "SNDK", "met": "yes", "quotes": ["vs 50d -22.3%"]}]}
    assert mm.sell_if_sells(answer, {"SNDK": rows[0][1]}, {"SNDK": "vs 50d -22.3%"}) == [
        ("SNDK", "vs 50d -22.3%")]
