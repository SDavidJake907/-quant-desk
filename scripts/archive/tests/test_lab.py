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
    ({"filters": [{"name": "rsi14", "op": "below", "value": "low"}]}, "must be a number"),
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
    # Gemma's revised bar (2026-10-23): +3 pts, Sharpe above the index's, drawdown <= index + 5.
    r = sim.Result(start=DAYS[0], end=DAYS[-1], annual_pct=12, spx_annual_pct=10, sharpe=0.5,
                   max_drawdown_pct=30, trades=5, win_rate_pct=50, avg_trade_pct=1,
                   exposure_pct=90, random_annual_pct=13, index_sharpe=0.7, index_drawdown_pct=20)
    ok, why = sim.passes(r)
    assert not ok
    for words in ("+2.0 pts", "Sharpe 0.50", "index's 0.70", "drawdown 30%", "random", "5 trades"):
        assert words in why
    good = r.model_copy(update={"annual_pct": 14, "sharpe": 0.9, "max_drawdown_pct": 24, "trades": 40})
    assert sim.passes(good) == (True, "passed")


def test_idle_cash_earns_the_index():
    flat = stock("F", [100.0] * 40)
    never = rc.parse({**BASE, "filters": [{"name": "rsi14", "op": "above", "value": 80}]})
    rising = {d: 100.0 * (1.001 ** i) for i, d in enumerate(DAYS)}
    r = sim.simulate(never, {"F": flat}, DAYS, rising, lambda d: frozenset({"F"}), {d: 1.0 for d in DAYS},
                     start=DAYS[0], end=DAYS[-1])
    assert r.trades == 0 and r.annual_pct == pytest.approx(r.spx_annual_pct, abs=0.01)


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
    assert "No searches of your own" in ledger.summary([])


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
    assert "No searches of your own" in ledger.summary(rows, universe="mid")


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
    from scanner.features.bars import Bars
    bars = _bars()
    market = {d: 100.0 + i * 0.1 for i, d in enumerate(bars.dates)}
    mc = [100.0 + i * 0.1 for i in range(len(bars.dates))]
    mbars = Bars(symbol="^GSPC", dates=bars.dates, opens=tuple(mc), closes=tuple(mc),
                 highs=tuple(x + 0.5 for x in mc), lows=tuple(x - 0.5 for x in mc), volumes=tuple(0.0 for _ in mc))
    vix = {d: 15.0 + (i % 7) for i, d in enumerate(bars.dates)}
    full = {**ind.compute(bars), **ind.more(bars, market, mbars, vix)}
    cut = {**ind.compute(bars.upto(279)), **ind.more(bars.upto(279), market, mbars, vix)}
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


def test_off_menu_values_snap_to_the_nearest_allowed():
    r = rc.parse({**BASE, "filters": [{"name": "rsi14", "op": "below", "value": 33},
                                      {"name": "price_vs_sma20_pct", "op": "below", "value": -3}]})
    assert "rsi14 below 30" in r.describe() and "price_vs_sma20_pct below -2" in r.describe()


# --- sell rules, trailing exit, buy timing (operator 2026-10-23: "buy/sell indicators in details") ---

def test_a_sell_rule_sells_at_the_first_close_it_is_true():
    s = stock("A", [100.0] * 40)
    s.ind["rsi14"][:] = [50.0] * 5 + [75.0] + [50.0] * 34           # true on day 5 only
    r = book({"top_n": 3, "rebalance_days": 21, "max_hold_days": 63,
              "exit_filters": [{"name": "rsi14", "op": "above", "value": 70}]}, [s])
    assert r.trades >= 1                                            # sold on day 5, not held 63 days


def test_flag_not_and_keys_unchanged_for_old_recipes():
    old = rc.parse(BASE)
    assert old.key == rc.parse({**BASE, "exit_filters": [], "trail_atr": 0.0, "entry_at": "next_close"}).key
    r = rc.parse({**BASE, "exit_filters": [{"name": "supertrend_up", "op": "not", "value": 1}]})
    assert "NOT supertrend_up" in r.describe() and r.key != old.key


def test_trailing_exit_sells_after_the_top():
    closes = [100.0, 100.0] + [100.0 + 2 * k for k in range(1, 11)] + [110.0] * 28   # up to 120, then 110
    s = stock("T", closes)
    trailing = book({"top_n": 3, "rebalance_days": 21, "max_hold_days": 63, "trail_atr": 3.0}, [s])
    holding = book({"top_n": 3, "rebalance_days": 21, "max_hold_days": 63}, [s])
    assert trailing.trades >= 1 and holding.trades == 0


def test_buying_at_the_next_open_uses_the_open():
    closes = [100.0] * 40
    opens = [100.0, 90.0] + [100.0] * 38
    s = stock("O", closes, opens=opens, lows=[min(o, c) for o, c in zip(opens, closes)])
    at_open = book({"top_n": 3, "rebalance_days": 21, "max_hold_days": 5, "entry_at": "next_open"}, [s])
    at_close = book({"top_n": 3, "rebalance_days": 21, "max_hold_days": 5}, [s])
    assert at_open.avg_trade_pct > 5 > at_close.avg_trade_pct      # bought at 90, sold at 100


def test_new_volume_indicators():
    bars = _bars(80)
    m = ind.more(bars)
    for name in ("volume_trend", "up_volume_ratio", "close_vs_vwap20_pct", "dollar_volume_m"):
        assert any(v is not None for v in m[name]), name
    assert m["dollar_volume_m"][79] == pytest.approx(
        sum(c * v for c, v in zip(bars.closes[60:80], bars.volumes[60:80])) / 20 / 1e6)


def test_summary_keeps_old_scoring_apart_and_lists_baselines():
    r = rc.parse(BASE)
    res = book({}, [stock("A", [100.0] * 40)]).model_dump(mode="json")
    rows = [{"n": 1, "session": "s", "stage": ledger.SEARCH, "key": r.key, "describe": "old", "result": res},
            {"n": 2, "session": "s", "stage": ledger.SEARCH, "key": r.key, "describe": "new", "result": res,
             "scoring": 2},
            {"n": 3, "session": "s", "stage": ledger.BASELINE, "key": r.key, "describe": "b", "result": res,
             "scoring": 2, "name": "momentum 12-1"}]
    text = ledger.summary(rows, scoring=2)
    assert "1 earlier searches" in text and "momentum 12-1" in text and "] old" not in text


def test_psar_and_crossovers_follow_the_trend():
    up = list(range(100, 160)) + list(range(160, 120, -1))
    bars_up = __import__("scanner.features.bars", fromlist=["Bars"]).Bars(
        symbol="P", dates=tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(len(up))),
        opens=tuple(float(x) for x in up), closes=tuple(float(x) for x in up),
        highs=tuple(x + 0.5 for x in up), lows=tuple(x - 0.5 for x in up), volumes=tuple(1000.0 for _ in up))
    m = ind.more(bars_up)
    assert m["psar_up"][50] == 1.0 and m["psar_up"][-1] == 0.0
    assert m["ema10_above_ema50"][58] == 1.0 and m["ema10_above_ema50"][-1] == 0.0
    flips = [i for i, v in enumerate(m["psar_flip_up_5d"]) if v == 1.0]
    assert all(m["psar_up"][i] == 1.0 for i in flips)


def test_rrs_credits_only_strength_beyond_the_stock_s_own_volatility():
    from scanner.features.bars import Bars
    n = 60
    days = tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(n))
    flat_idx = Bars(symbol="^GSPC", dates=days, opens=(100.0,) * n, closes=(100.0,) * n,
                    highs=(101.0,) * n, lows=(99.0,) * n, volumes=(0.0,) * n)
    up = [100.0 + i for i in range(n)]
    stock_bars = Bars(symbol="X", dates=days, opens=tuple(up), closes=tuple(up), highs=tuple(x + 1 for x in up),
                      lows=tuple(x - 1 for x in up), volumes=(1000.0,) * n)
    m = ind.more(stock_bars, None, flat_idx)
    assert m["rrs_5d"][-1] > 0 and m["rrs_20d"][-1] > 0          # rising while the index is flat
    assert m["rrs_5d"][3] is None                                 # not enough history


def test_weekend_focus_limits_the_hold_to_3_or_4_weeks():
    import importlib
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path("scripts").resolve()))
    lab = importlib.import_module("gemma_lab")
    assert lab.focus_schema()["properties"]["recipe"]["properties"]["max_hold_days"]["enum"] == [15, 21]
    assert lab.in_focus(rc.parse({**BASE, "max_hold_days": 63})).max_hold_days == 21
    assert lab.in_focus(rc.parse({**BASE, "max_hold_days": 5})).max_hold_days == 15


# --- profit protection (operator 2026-10-23: "holding blew out the profit") ----------------

def _runup_then_fade():
    # bought at 100 on day 1; closes run to 110 by day 6, then fade back to 98
    closes = [100.0, 100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 107.0, 104.0, 101.0, 98.0] + [98.0] * 29
    return stock("R", closes)


def test_lock_never_lets_a_3pct_winner_turn_red():
    # one decision only (rebalance and hold longer than the series): plain holding never sells,
    # the lock sells at the first CLOSE under the buy after the run-up (98 here: close-based)
    one = {"top_n": 3, "rebalance_days": 21, "max_hold_days": 63}
    r = book({**one, "lock_at_pct": 3.0, "lock_keep": 0.0}, [_runup_then_fade()])
    plain = book(one, [_runup_then_fade()])
    assert plain.trades == 0 and r.trades >= 1


def test_keep_half_and_percent_trail_sell_on_the_fade():
    half = book({"top_n": 3, "rebalance_days": 21, "max_hold_days": 21, "lock_at_pct": 5.0, "lock_keep": 0.5},
                [_runup_then_fade()])
    trail = book({"top_n": 3, "rebalance_days": 21, "max_hold_days": 21, "trail_pct": 4.0}, [_runup_then_fade()])
    assert half.avg_trade_pct > 3.0 and trail.avg_trade_pct > 2.0     # sold near 107 / 104, not 98
    r = rc.parse({**BASE, "lock_at_pct": 5.0, "lock_keep": 0.5, "trail_pct": 6.0})
    assert "keep 50% of the best gain" in r.describe() and "6% under the best close" in r.describe()
    assert rc.parse(BASE).key == rc.parse({**BASE, "lock_at_pct": 0.0, "lock_keep": 0.0, "trail_pct": 0.0}).key
