"""Heatmap layout and colour-scale tests.

A heatmap is a claim about data made in colour, so the ways it can lie are the
things worth pinning: an outlier flattening every other cell, a missing value
rendered as an average one, and sectors that reshuffle between runs so two
screenshots cannot be compared.
"""

from __future__ import annotations

import pytest

from scanner.heatmap import (
    CLAMP_PP,
    NEUTRAL_PP,
    SECTOR_ORDER,
    Cell,
    build_cells,
    intensity,
    summarise,
)


def _cell(symbol: str, rs: float | None, sector: str = "Financials", passed: int = 3) -> Cell:
    return Cell(
        symbol=symbol, name=f"{symbol} Inc", sector=sector, index="S&P 500",
        relative_strength=rs, momentum_12_1=rs, price=100.0,
        pct_from_52w_high=-5.0, criteria_passed=passed, criteria_total=6,
    )


# ---- colour scale ------------------------------------------------------

def test_zero_relative_strength_is_neutral():
    assert intensity(0.0) == 0.0


def test_direction_follows_sign():
    assert intensity(30.0) > 0
    assert intensity(-30.0) < 0


def test_scale_is_symmetric():
    assert intensity(25.0) == pytest.approx(-intensity(-25.0))


def test_extremes_clamp_instead_of_rescaling():
    """One name at +300pp must not wash out every other cell.

    Without clamping the whole map collapses toward neutral to make room for
    a single outlier, and the map stops showing anything.
    """
    assert intensity(CLAMP_PP) == 1.0
    assert intensity(300.0) == 1.0
    assert intensity(-300.0) == -1.0


def test_noise_band_reads_as_flat():
    """A name within a couple of points of the index has not beaten it."""
    assert intensity(NEUTRAL_PP - 0.1) == 0.0
    assert intensity(-NEUTRAL_PP + 0.1) == 0.0
    assert intensity(NEUTRAL_PP + 10) != 0.0


def test_missing_data_is_not_drawn_as_average_performance():
    """None means "not enough history", which is not the same claim as
    "matched the index" — the renderer draws it as an explicit gap."""
    assert intensity(None) == 0.0
    cells = build_cells([_cell("A", None), _cell("B", 10.0)])
    assert summarise(cells)["unrated"] == 1
    assert summarise(cells)["rated"] == 1


# ---- layout ------------------------------------------------------------

def test_cells_group_by_sector():
    blocks = build_cells([
        _cell("A", 10.0, "Financials"),
        _cell("B", 20.0, "Energy"),
        _cell("C", 5.0, "Financials"),
    ])
    assert {b.sector for b in blocks} == {"Financials", "Energy"}
    assert next(b for b in blocks if b.sector == "Financials").count == 2


def test_sector_order_is_fixed_not_performance_ranked():
    """Two runs must be visually comparable; a map that reorders itself by
    yesterday's winners cannot be read against yesterday's screenshot."""
    weak_tech = [_cell("A", -40.0, "Information Technology")]
    strong_energy = [_cell("B", 90.0, "Energy")]
    blocks = build_cells(weak_tech + strong_energy)
    order = [b.sector for b in blocks]
    assert order.index("Information Technology") < order.index("Energy")
    assert order == [s for s in SECTOR_ORDER if s in order]


def test_strongest_name_comes_first_within_a_sector():
    blocks = build_cells([_cell("WEAK", 1.0), _cell("STRONG", 50.0), _cell("MID", 20.0)])
    assert [c.symbol for c in blocks[0].cells] == ["STRONG", "MID", "WEAK"]


def test_unrated_names_sort_last():
    blocks = build_cells([_cell("NONE", None), _cell("RATED", -30.0)])
    assert [c.symbol for c in blocks[0].cells] == ["RATED", "NONE"]


def test_unknown_sectors_are_kept_not_dropped():
    blocks = build_cells([_cell("X", 5.0, "Space Mining")])
    assert [b.sector for b in blocks] == ["Space Mining"]


def test_empty_universe_produces_no_blocks():
    assert build_cells([]) == ()
    assert summarise(())["companies"] == 0


# ---- summaries ---------------------------------------------------------

def test_sector_median_ignores_unrated_names():
    block = build_cells([_cell("A", 10.0), _cell("B", 20.0), _cell("C", None)])[0]
    assert block.median_rs == pytest.approx(15.0)


def test_passing_counts_only_full_passes():
    block = build_cells([
        _cell("ALL", 10.0, passed=6),
        _cell("MOST", 10.0, passed=5),
    ])[0]
    assert block.passing == 1


def test_summary_counts_names_beating_the_index():
    blocks = build_cells([_cell("A", 10.0), _cell("B", -10.0), _cell("C", 30.0)])
    assert summarise(blocks)["beating_index"] == 2
    assert summarise(blocks)["companies"] == 3
