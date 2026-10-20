"""Gemma's pool: the whole S&P 500 through the scanner's floor, shortlisted by her."""

from types import SimpleNamespace

from scanner.advisor import gemma_pool as gp

FLOOR = dict(min_price=20.0, min_range_pct=0.0, min_dollar_volume=1_000_000.0)


def test_the_scanner_floor_drops_cheap_and_thin_stocks():
    ok = gp.tradeable([50.0] * 20, [51.0] * 20, [49.0] * 20, [100_000] * 20, **FLOOR)
    cheap = gp.tradeable([10.0] * 20, [11.0] * 20, [9.0] * 20, [10_000_000] * 20, **FLOOR)
    thin = gp.tradeable([50.0] * 20, [51.0] * 20, [49.0] * 20, [1_000] * 20, **FLOOR)
    assert ok and not cheap and not thin
    assert not gp.tradeable([], [], [], [], **FLOOR)


def test_a_line_is_compact_and_marks_the_momentum_rule():
    r = gp.PoolRow(symbol="SNDK", sector="Information Technology", r1m=-25.4, r3m=40.0,
                   mom_12_1=310.2, atr_pct=7.26, momentum_rank=1)
    assert gp.line(r) == ("SNDK (Information Technology) 1m -25% 3m +40% 12-1 +310% "
                          "ATR 7.3% | momentum rule #1")
    assert "n/a" in gp.line(r.model_copy(update={"r1m": None}))


def test_prompt_shows_the_whole_pool_and_the_holdings():
    rows = [gp.PoolRow(symbol=f"S{i}", sector="X", r1m=1, r3m=2, mom_12_1=3, atr_pct=1)
            for i in range(500)]
    text = gp.prompt(rows, held=["MU", "MPC"], market="S&P 500 ABOVE")
    assert "all 500 tradeable S&P 500 stocks" in text
    assert "S0 (X)" in text and "S499 (X)" in text
    assert "(MPC, MU)" in text and "up to 20" in text


def test_schema_names_only_and_capped():
    s = gp.schema(["A", "B"])["properties"]["shortlist"]
    assert s["items"]["enum"] == ["A", "B"] and s["maxItems"] == gp.SHORTLIST


def test_validate_keeps_pool_names_drops_holdings_repeats_and_extras():
    pool = [f"S{i}" for i in range(30)] + ["MU"]
    answer = {"shortlist": ["s1", "S1", "ZZZ", "MU", *[f"S{i}" for i in range(2, 30)]]}
    out = gp.validate(answer, pool, held=["MU"])
    assert out[0] == "S1" and "ZZZ" not in out and "MU" not in out
    assert len(out) == gp.SHORTLIST and len(set(out)) == len(out)


def test_an_unusable_answer_is_an_empty_shortlist():
    assert gp.validate(None, ["A"]) == ()
    assert gp.validate({"shortlist": "A"}, ["A"]) == ()


def test_rows_are_listed_strongest_momentum_first():
    bars = {s: SimpleNamespace(closes=[100.0] * 70) for s in ("A", "B", "C")}
    rows = gp.rows_for(bars, {"A": "X"}, momentum={"A": 5.0, "B": 50.0, "C": None},
                       atr={"A": 2.0}, momentum_rank={"B": 3})
    assert [r.symbol for r in rows] == ["B", "A", "C"]
    assert rows[0].momentum_rank == 3 and rows[0].r1m == 0.0 and rows[2].sector == "unknown"



def test_market_cap_floor_drops_small_and_keeps_unknown():
    caps = {"BIG": 5e9, "EDGE": 2e9, "SMALL": 1.5e9}
    assert gp.large_enough("BIG", caps)
    assert gp.large_enough("EDGE", caps)
    assert not gp.large_enough("SMALL", caps)
    assert gp.large_enough("NEW", caps)          # missing: kept
    assert gp.large_enough("SMALL", {})          # no data today: kept
    assert gp.MIN_MARKET_CAP == 2_000_000_000.0
