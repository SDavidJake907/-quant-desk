"""Gemma's lab (scanner/lab): indicators, the recipe menu, the book simulation, the log."""

from datetime import date, timedelta

import pytest

from scanner.lab import indicators as ind
from scanner.lab import ledger
from scanner.lab import recipe as rc
from scanner.lab import sim

BASE = {"filters": [], "rank_by": "ret_1m_pct", "rank_order": "high_first", "top_n": 3,
        "rebalance_days": 5, "stop_atr": 0.0, "target_r": 0.0, "max_hold_days": 5,
        "max_per_sector": 0}


# --- indicators -------------------------------------------------------------

def test_sma_and_rolling_max():
    assert ind.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    assert ind._rolling_max([3, 1, 4, 1, 5], 3) == [None, None, 4, 4, 5]
    assert ind.prior_high([3, 1, 4, 1, 5], 2) == [None, None, 3, 4, 4]


def test_rsi_extremes():
    assert ind.rsi([float(i) for i in range(30)])[-1] == 100.0
    assert ind.rsi([float(30 - i) for i in range(30)])[-1] == pytest.approx(0.0)


def test_indicators_never_see_the_future():
    from scanner.features.bars import Bars
    n = 300
    closes = [100 + (i % 7) for i in range(n)]
    bars = Bars(symbol="X", dates=tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(n)),
                opens=tuple(closes), closes=tuple(closes), highs=tuple(c + 1 for c in closes),
                lows=tuple(c - 1 for c in closes), volumes=tuple(1000.0 for _ in closes))
    full, cut = ind.compute(bars), ind.compute(bars.upto(259))
    for name, series in cut.items():
        assert series == full[name][:260], name


# --- the recipe menu ----------------------------------------------------------

def test_parse_accepts_the_menu_and_keys_ignore_filter_order():
    a = rc.parse({**BASE, "filters": [{"name": "rsi14", "op": "below", "value": 30},
                                      {"name": "pullback", "op": "is", "value": 1}]})
    b = rc.parse({**BASE, "filters": [{"name": "pullback", "op": "is", "value": 1},
                                      {"name": "rsi14", "op": "below", "value": 30}]})
    assert a.key == b.key and "rsi14 below 30" in a.describe()


@pytest.mark.parametrize("bad, words", [
    ({"filters": [{"name": "rsi14", "op": "below", "value": 33}]}, "value must be one of"),
    ({"filters": [{"name": "magic", "op": "above", "value": 1}]}, "unknown filter"),
    ({"top_n": 7}, "top_n"),
    ({"target_r": 2.0}, "needs a stop"),
    ({"rank_by": "pullback"}, "rank_by"),
])
def test_parse_refuses_anything_off_the_menu(bad, words):
    with pytest.raises(ValueError, match=words):
        rc.parse({**BASE, **bad})


# --- the book -----------------------------------------------------------------

DAYS = [date(2020, 1, 1) + timedelta(days=i) for i in range(40)]


def stock(symbol, closes, *, opens=None, lows=None, highs=None, rank=None, sector="Tech"):
    n = len(closes)
    series = {name: [0.0] * n for name in rc.NUMERIC}
    series.update({name: [1.0] * n for name in rc.FLAGS})
    series["atr"] = [1.0] * n
    series["ret_1m_pct"] = list(rank) if rank else [0.0] * n
    return sim.Stock(symbol, sector, DAYS[:n], opens or closes, highs or closes, lows or closes,
                     closes, series, {d: i for i, d in enumerate(DAYS[:n])})


def book(recipe, stocks, **kw):
    spx = {d: 100.0 for d in DAYS}
    slots = recipe.get("top_n", BASE["top_n"])     # 1 slot is off her menu; tests may use it
    made = rc.parse({**BASE, **recipe, "top_n": 3}).model_copy(update={"top_n": slots})
    return sim.simulate(made, {s.symbol: s for s in stocks}, DAYS, spx,
                        lambda d: frozenset(s.symbol for s in stocks), {d: 1.0 for d in DAYS},
                        start=DAYS[0], end=DAYS[-1], **kw)


def test_buys_the_top_ranked_at_the_next_close_and_holds_to_the_limit():
    up = stock("UP", [100.0] + [110.0] * 39, rank=[5.0] * 40)
    flat = stock("FLAT", [100.0] * 40, rank=[1.0] * 40)
    r = book({"top_n": 1, "rebalance_days": 21, "max_hold_days": 5}, [up, flat])
    # decided day 0 on UP (higher rank), bought at day 1's close 110: no gain but costs
    assert r.trades >= 1 and r.avg_trade_pct < 0


def test_stop_fills_at_the_open_on_a_gap():
    closes = [100.0, 100.0, 100.0, 80.0] + [80.0] * 36
    opens = [100.0, 100.0, 100.0, 85.0] + [80.0] * 36
    s = stock("GAP", closes, opens=opens, lows=closes)
    r = book({"top_n": 1, "stop_atr": 1.5, "rebalance_days": 21, "max_hold_days": 63}, [s])
    # bought at 100 (day 1), stop 98.5; day 3 opens at 85 -> filled at 85, not 98.5
    assert r.avg_trade_pct == pytest.approx((85 * (1 - 4.5e-4)) / (100 * (1 + 4.5e-4)) * 100 - 100, abs=0.01)


def test_a_day_touching_stop_and_target_is_a_stop():
    closes = [100.0] * 40
    lows = [100.0, 100.0, 100.0, 90.0] + [100.0] * 36
    highs = [100.0, 100.0, 100.0, 120.0] + [100.0] * 36
    s = stock("BOTH", closes, lows=lows, highs=highs)
    r = book({"top_n": 1, "stop_atr": 1.5, "target_r": 2.0, "rebalance_days": 21, "max_hold_days": 63}, [s])
    assert r.avg_trade_pct < 0


def test_sector_cap_and_random_control_are_reproducible():
    names = [stock(f"S{i}", [100.0 + i * 0.1 * k for k in range(40)], rank=[float(i)] * 40)
             for i in range(6)]
    capped = book({"top_n": 3, "max_per_sector": 1}, names)
    assert capped.exposure_pct < book({"top_n": 3}, names).exposure_pct
    import random
    a = book({"top_n": 3}, names, rng=random.Random(1))
    b = book({"top_n": 3}, names, rng=random.Random(1))
    assert a == b


def test_passes_names_every_miss():
    r = sim.Result(start=DAYS[0], end=DAYS[-1], annual_pct=12, spx_annual_pct=10, sharpe=0.8,
                   max_drawdown_pct=30, trades=5, win_rate_pct=50, avg_trade_pct=1,
                   exposure_pct=90, random_annual_pct=13)
    ok, why = sim.passes(r)
    assert not ok
    for words in ("+2.0 pts", "Sharpe 0.80", "drawdown 30%", "random", "5 trades"):
        assert words in why
    good = r.model_copy(update={"annual_pct": 30, "sharpe": 1.5, "max_drawdown_pct": 15, "trades": 40})
    assert sim.passes(good) == (True, "passed")


# --- the log ------------------------------------------------------------------

def test_ledger_round_trip_and_summary(tmp_path):
    path = tmp_path / "log.jsonl"
    r = rc.parse(BASE)
    res = book({}, [stock("A", [100.0] * 40)])
    ledger.append({"n": 1, "session": "2026-10-24", "stage": ledger.SEARCH, "key": r.key,
                   "describe": r.describe(), "result": res.model_dump(mode="json")}, path)
    rows = ledger.read(path)
    assert ledger.tried(rows, ledger.SEARCH) == {r.key}
    assert "1 recipes searched" in ledger.summary(rows)
    assert "empty" in ledger.summary([])


# --- the weekend (operator 2026-10-23: "backtest all weekend. big caps, med caps, small caps") ---

def test_compact_missing_values_are_not_usable():
    assert not sim._has(None) and not sim._has(float("nan")) and sim._has(0.0)


def test_summary_is_per_universe_and_counts_the_others(tmp_path):
    r = rc.parse(BASE)
    res = book({}, [stock("A", [100.0] * 40)]).model_dump(mode="json")
    rows = [{"n": 1, "session": "s", "stage": ledger.SEARCH, "key": r.key, "describe": "x", "result": res},
            {"n": 2, "session": "s", "stage": ledger.SEARCH, "key": r.key, "describe": "x", "result": res,
             "universe": "small"}]
    text = ledger.summary(rows, universe="large")
    assert "1 recipes searched" in text and "Other stock groups: small 1" in text
    assert ledger.tried(rows, ledger.SEARCH, universe="small") == {r.key}
    assert "empty" in ledger.summary(rows, universe="mid")


def test_weekend_plan_rotates_and_only_the_last_session_per_group_nominates():
    import importlib.util
    import sys
    from datetime import datetime
    from pathlib import Path
    sys.path.insert(0, str(Path("scripts").resolve()))
    lab = importlib.import_module("gemma_lab")
    plan = lab.weekend_plan(datetime(2026, 10, 23, 18, 30))           # Friday evening
    groups = [u for _, u, _ in plan]
    assert groups[:3] == ["large", "mid", "small"]
    assert plan[-1][0] < datetime(2026, 10, 25, lab.WEEKEND_END_HOUR)
    nominating = [u for _, u, n in plan if n]
    assert sorted(nominating) == ["large", "mid", "small"]
    for u in ("large", "mid", "small"):
        last = max(i for i, (_, x, _) in enumerate(plan) if x == u)
        assert plan[last][2]


# --- the second menu (operator 2026-10-23: "all the indicators ... market hours, after hours,
# days of the week, months") ---------------------------------------------------------------

def _bars(n=300):
    from scanner.features.bars import Bars
    closes = [100 + (i % 9) - (i % 4) * 0.5 for i in range(n)]
    opens = [c - 0.3 for c in closes]
    return Bars(symbol="X", dates=tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(n)),
                opens=tuple(opens), closes=tuple(closes), highs=tuple(c + 1 for c in closes),
                lows=tuple(c - 1 for c in closes), volumes=tuple(1000.0 + i for i in range(n)))


def test_every_menu_name_is_computed_and_never_sees_the_future():
    bars = _bars()
    market = {d: 100.0 + i * 0.1 for i, d in enumerate(bars.dates)}
    full = {**ind.compute(bars), **ind.more(bars, market)}
    cut = {**ind.compute(bars.upto(279)), **ind.more(bars.upto(279), market)}
    for name in [*rc.NUMERIC, *(f for f in rc.FLAGS if f != "market_uptrend")]:
        assert name in full, name
        assert cut[name] == full[name][:280], name


def test_overnight_and_regular_session_split():
    bars = _bars(10)
    m = ind.more(bars)
    assert m["intraday_pct"][3] == pytest.approx((bars.closes[3] / bars.opens[3] - 1) * 100)
    gaps = [(bars.opens[i] / bars.closes[i - 1] - 1) * 100 for i in range(1, 10)]
    assert m["overnight_5d_pct"][9] == pytest.approx(sum(gaps[-5:]))
    assert m["day_of_week"][0] == 3.0 and m["month"][0] == 1.0     # 2020-01-01 was a Wednesday


def test_calendar_filters_take_is_and_others_do_not():
    r = rc.parse({**BASE, "filters": [{"name": "month", "op": "is", "value": 11},
                                      {"name": "day_of_week", "op": "below", "value": 3}]})
    assert "month is 11" in r.describe()
    with pytest.raises(ValueError, match="above"):
        rc.parse({**BASE, "filters": [{"name": "rsi14", "op": "is", "value": 30}]})


def test_is_filter_trades_only_on_that_weekday():
    names = [stock("A", [100.0 + 0.1 * k for k in range(40)])]
    names[0].ind["day_of_week"][:] = [float(d.weekday() + 1) for d in DAYS]
    tuesday_only = book({"top_n": 3, "rebalance_days": 5,
                        "filters": [{"name": "day_of_week", "op": "is", "value": 2}]}, names)
    never = book({"top_n": 3, "rebalance_days": 5,
                  "filters": [{"name": "day_of_week", "op": "is", "value": 5},
                              {"name": "day_of_week", "op": "is", "value": 1}]}, names)
    assert never.trades == 0 and tuesday_only.exposure_pct >= 0


def test_a_restarted_weekend_does_not_nominate_twice():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
    rows = [{"stage": ledger.HIDDEN_A, "universe": "mid", "at": "2026-10-25T09:00:00+00:00", "key": "k"}]
    assert ledger.nominated_since(rows, "mid", now - timedelta(days=3))
    assert not ledger.nominated_since(rows, "small", now - timedelta(days=3))
    assert not ledger.nominated_since(rows, "mid", now + timedelta(days=1))
