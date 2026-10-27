"""morning_leaders.py: which top-20 names count as 'still rising', and the pick of 4."""

import importlib.util
import sys
from datetime import date
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "morning_leaders.py"
spec = importlib.util.spec_from_file_location("morning_leaders", SCRIPT)
ml = importlib.util.module_from_spec(spec)
sys.modules["morning_leaders"] = ml  # dataclasses look their module up here
spec.loader.exec_module(ml)


def row(symbol, mom, r1m=5.0, above=True):
    return ml.Row(symbol, "Energy", mom, r1m, 1.0, above, 100.0)


def test_make_row_measures_sma_month_and_week():
    closes = [100.0] * 60 + [110.0]
    r = ml.make_row("X", "Tech", 200.0, closes)
    assert r.above_sma
    assert round(r.r1m, 1) == 10.0 and round(r.r5d, 1) == 10.0
    falling = ml.make_row("Y", "Tech", 200.0, [100.0] * 60 + [90.0])
    assert not falling.above_sma


def test_short_history_has_no_month_return():
    r = ml.make_row("X", "Tech", 50.0, [10.0, 11.0])
    assert r.r1m is None and not ml.is_rising(r)


def test_rising_needs_both_sma_and_month():
    assert ml.is_rising(row("A", 100))
    assert not ml.is_rising(row("B", 100, above=False))
    assert not ml.is_rising(row("C", 100, r1m=-1.0))
    assert not ml.is_rising(row("D", 100, r1m=0.0))


def test_select_ranks_by_momentum_and_takes_four():
    rows = [row("LOW", 120), row("TOP", 400), row("MID", 200), row("FALL", 999, above=False),
            row("FOURTH", 150), row("FIFTH", 110)]
    chosen, skipped = ml.select(rows, {}, ml.is_rising)
    assert [r.symbol for r in chosen] == ["TOP", "MID", "FOURTH", "LOW"]
    assert skipped == []


def test_select_skips_names_other_books_hold_and_backfills():
    rows = [row("VLO", 300), row("PBF", 200), row("ILMN", 150)]
    chosen, skipped = ml.select(rows, {"VLO": "gemma"}, ml.is_rising, n=2)
    assert [r.symbol for r in chosen] == ["PBF", "ILMN"]
    assert [r.symbol for r in skipped] == ["VLO"]


def test_select_takes_any_keep_rule():
    rows = [row("A", 300), row("B", 200), row("C", 100)]
    chosen, _ = ml.select(rows, {}, lambda r: r.symbol != "A")
    assert [r.symbol for r in chosen] == ["B", "C"]


def test_record_has_one_shape_for_grading():
    from scanner.features.money_flow import StockFlow
    flows = {s: StockFlow(symbol=s, sector_inflow=True, dollar_surge=1.5,
                          up_volume_share=0.6, obv_rising=True, close_location=0.2)
             for s in ("PBF", "MRNA")}
    scan = ml.Scan(rows=[], prices={}, flows=flows, sectors={}, earnings={}, failed=(),
                   as_of=date(2026, 10, 16), priced=0, errors=0, grades=[], graded_days=0)
    rec = ml.record(date(2026, 10, 19), scan, ([row("PBF", 178)], []), ([row("MRNA", 468)], [row("VLO", 1)]))
    assert rec["lists"] == {"rising": ["PBF"], "flow": ["MRNA"]}
    assert rec["flow_points"] == {"PBF": 4, "MRNA": 4}
    assert rec["held_elsewhere"] == ["VLO"]


def test_load_records_skips_bad_lines(tmp_path):
    path = tmp_path / "picks.jsonl"
    path.write_text('{"day": "2026-10-19", "lists": {"rising": ["A"], "flow": []}}\n'
                    'not json\n'
                    '{"day": "2026-10-20"}\n', encoding="utf-8")
    assert [r["day"] for r in ml.load_records(path)] == ["2026-10-19"]


def test_estimate_matches_picks_sizing():
    from scanner.execution.policy import TradingPolicy
    policy = TradingPolicy()
    e = ml.estimate(100.0, 4.0, sleeve=50_000, room=50_000, policy=policy)
    assert e.limit == round(100 * (1 + policy.entry_limit_offset_pct / 100), 2)
    # stop sits 2.5 daily ATRs (4% each) under the limit
    assert abs(e.stop - e.limit * (1 - policy.picks_stop_atr_multiple * 0.04)) < 0.02
    assert e.sizing.is_tradeable
    cap = min(policy.max_position_pct, policy.shared_position_cap_pct)
    assert e.sizing.position_dollars <= 50_000 * cap / 100 + e.limit
    assert e.sizing.risk_dollars <= 50_000 * policy.risk_pct / 100 + 1


def test_estimate_without_atr_or_sleeve_is_none():
    assert ml.estimate(100.0, None, 50_000, 50_000) is None
    assert ml.estimate(100.0, 4.0, 0.0, 0.0) is None


def test_no_room_says_why_instead_of_a_size():
    e = ml.estimate(100.0, 4.0, sleeve=50_000, room=0.0)
    assert not e.sizing.is_tradeable
    assert ml._buy_cell(e, 50_000).startswith("none:")


def test_money_formats():
    assert ml.money(218.01) == "$218.01"
    assert ml.money(3_920) == "$3.9K"
    assert ml.money(3_691e6) == "$3.7B"
    assert ml.money(254e6) == "$254.0M"


def test_level_shows_price_against_it():
    assert ml.level(154.93, 218.01) == "$154.93 (+40.7%)"
    assert ml.level(242.29, 218.01) == "$242.29 (-10.0%)"
    assert ml.level(0.0, 10.0) == "?"


def test_change_shows_percent_and_dollars():
    assert ml.change(218.01, 37.9) == "+37.9% (+$59.92)"
    assert ml.change(100.0, -20.0) == "-20.0% (-$25.00)"
    assert ml.change(100.0, None) == "?"


def test_buy_cell_shows_share_of_sleeve():
    e = ml.estimate(100.0, 4.0, sleeve=50_000, room=50_000)
    cell = ml._buy_cell(e, 50_000)
    assert "% of sleeve" in cell and "risk $" in cell
