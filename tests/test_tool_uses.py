"""Gemma's tool uses beyond the 9:45 decision (Gemma 2026-10-24): the 10:00 buy recheck, lookups in
the sell-if check, and the 3:15 PM ET pre-close check."""

from datetime import date, datetime

from scanner.advisor import gemma_trader as gt
from scanner.execution import buy_queue
from scanner.ops import market_monitor as mm
from scanner.ops import pre_close
from scanner.ops import shock_alert as sa

TECH = "Information Technology"


def recheck(item, price=None, kinds=(), sector=None, earn=None):
    return buy_queue.recheck(item, price=price, alert_kinds=kinds, sector=sector,
                             earnings_in_days=earn, buffer_days=5)


def test_recheck_skips_a_two_percent_drop_since_her_decision():
    assert "fell -2.0%" in recheck({"price": 100.0}, price=98.0)
    assert recheck({"price": 100.0}, price=98.1) is None
    assert recheck({}, price=50.0) is None                # no decision price: the drop test is skipped


def test_recheck_skips_only_a_new_theme_alert_on_a_theme_stock():
    assert "theme_shock" in recheck({"alerts": []}, kinds={"theme_shock"}, sector=TECH)
    assert recheck({"alerts": ["theme_shock"]}, kinds={"theme_shock"}, sector=TECH) is None   # she saw it
    assert recheck({"alerts": []}, kinds={"theme_shock"}, sector="Utilities") is None
    assert recheck({"alerts": []}, kinds={"stress"}, sector=TECH) is None


def test_recheck_skips_earnings_inside_the_buffer():
    assert "earnings in 3" in recheck({}, earn=3)
    assert recheck({}, earn=6) is None


def test_lookups_join_only_that_stocks_facts():
    calls = [{"tool": "get_news", "args": {"symbol": "mu"}, "result": "Micron cuts guidance"},
             {"tool": "market_map", "args": {}, "result": "map"}]
    facts = {"MU": "price 100", "NVDA": "price 180"}
    out = mm.with_lookups(facts, calls)
    assert "Micron cuts guidance" in out["MU"] and out["NVDA"] == "price 180" and facts["MU"] == "price 100"
    answer = {"holdings": [{"symbol": "MU", "met": "yes", "quotes": ["Micron cuts guidance"]}]}
    assert mm.sell_if_sells(answer, {"MU": "guidance cut"}, out) == [("MU", "Micron cuts guidance")]
    assert mm.sell_if_sells(answer, {"MU": "guidance cut"}, facts) == []


def test_research_prompt_fills_the_budget():
    assert "up to 2 lookups" in gt.research_prompt("q", gt.SELL_IF_ASK, gt.SELL_IF_CALLS)
    assert "up to 3 lookups" in gt.research_prompt("q", gt.PRE_CLOSE_ASK, gt.PRE_CLOSE_CALLS)
    assert gt.research_prompt("q").endswith(gt.RESEARCH_ASK)


def test_pre_close_runs_once_at_three_fifteen_et():
    at = datetime(2026, 10, 26, 15, 15, tzinfo=mm.ET)
    assert pre_close.due(at, None)
    assert not pre_close.due(at, "2026-10-26")
    assert not pre_close.due(datetime(2026, 10, 26, 15, 45, tzinfo=mm.ET), None)


def test_pre_close_only_for_bellwether_or_theme_shock():
    alerts = [sa.Alert(kind="stress", text="x"), sa.Alert(kind="bellwether", text="META reports")]
    assert [a.kind for a in pre_close.triggers(alerts)] == ["bellwether"]


def test_pre_close_respects_the_minimum_hold_outside_the_theme():
    allowed = {"MU": pre_close.may_sell(held_days=1, min_hold=3, sector=TECH),
               "KO": pre_close.may_sell(held_days=1, min_hold=3, sector="Consumer Staples"),
               "XOM": pre_close.may_sell(held_days=4, min_hold=3, sector="Energy")}
    answer = {"holdings": [{"symbol": s, "action": "sell", "reason": "r"} for s in ("MU", "KO", "XOM", "ZZZ")]}
    out, kept = pre_close.sells(answer, allowed)
    assert [s for s, _ in out] == ["MU", "XOM"] and kept == ["KO"]


def test_pre_close_prompt_marks_held_names():
    text = pre_close.prompt({"KO": "facts"}, [("KO", 1.0, None, 1, False)],
                            alerts=[sa.Alert(kind="bellwether", text="META reports tonight")],
                            market="up", today=date(2026, 10, 26))
    assert "Monday" in text and "keep only" in text and "META reports tonight" in text
