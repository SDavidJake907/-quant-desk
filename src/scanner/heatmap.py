"""Heatmap layout and colour scaling — pure, deterministic, no rendering.

Everything here is a function of the feature set, so the same universe always
produces the same map. The HTML is generated elsewhere; this module decides
what goes where and what shade it gets, which are the parts worth testing.

WHY RELATIVE STRENGTH, AND WHY CLAMPED
The cell colour is relative strength: a stock's 12-1 momentum minus the
benchmark's, in percentage points. It is the one cross-sectional number the
trend screen actually ranks on, and it is already computed.

Its distribution has a long right tail — one name at +300pp against a bulk
sitting inside ±30pp. Scaled linearly across the full range, that single
outlier washes every other cell to the same neutral tone and the map shows
nothing. So the scale clamps at ±CLAMP_PP and says so in the legend. Clamping
distorts the extremes; not clamping distorts everything else.

A DIVERGING SCALE NEEDS A MEANINGFUL ZERO
Zero here is "matched the index", which is exactly the kind of real midpoint a
diverging scale requires. Red below, green above, a neutral band either side
of zero so that noise does not read as signal.
"""

from __future__ import annotations

import statistics
from typing import Iterable

from scanner.models import Frozen

#: Relative strength beyond this is drawn at full intensity.
CLAMP_PP = 50.0

#: Inside this band a name has effectively matched the index.
NEUTRAL_PP = 2.0

#: Sectors render in a fixed order so the map does not reshuffle between runs.
SECTOR_ORDER: tuple[str, ...] = (
    "Information Technology",
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Health Care",
    "Financials",
    "Industrials",
    "Energy",
    "Materials",
    "Utilities",
    "Real Estate",
    "Unclassified",
)


class Cell(Frozen):
    """One company in the map."""

    symbol: str
    name: str
    sector: str
    index: str
    relative_strength: float | None
    momentum_12_1: float | None
    price: float
    pct_from_52w_high: float
    criteria_passed: int
    criteria_total: int

    @property
    def passed_all(self) -> bool:
        return self.criteria_total > 0 and self.criteria_passed == self.criteria_total


class SectorBlock(Frozen):
    """One sector's cells, plus the summary shown on its header."""

    sector: str
    cells: tuple[Cell, ...]

    @property
    def count(self) -> int:
        return len(self.cells)

    @property
    def median_rs(self) -> float | None:
        values = [c.relative_strength for c in self.cells if c.relative_strength is not None]
        return statistics.median(values) if values else None

    @property
    def passing(self) -> int:
        return sum(1 for c in self.cells if c.passed_all)


def intensity(relative_strength: float | None) -> float:
    """Map relative strength to [-1, 1] for colouring.

    None means the feature could not be computed — a name with too little
    history. That is a different thing from "performed in line with the
    index", so it returns 0.0 here and is drawn as an explicit gap, never as
    a neutral result.
    """
    if relative_strength is None:
        return 0.0
    if abs(relative_strength) <= NEUTRAL_PP:
        return 0.0
    clamped = max(-CLAMP_PP, min(CLAMP_PP, relative_strength))
    return clamped / CLAMP_PP


def build_cells(rows: Iterable[Cell]) -> tuple[SectorBlock, ...]:
    """Group cells into sector blocks, strongest name first within each.

    Sorting by relative strength inside a block puts the leaders top-left,
    which is where the eye lands. Sectors themselves keep SECTOR_ORDER rather
    than sorting by performance, so the map stays comparable day to day.
    """
    by_sector: dict[str, list[Cell]] = {}
    for cell in rows:
        by_sector.setdefault(cell.sector, []).append(cell)

    ordered = [s for s in SECTOR_ORDER if s in by_sector]
    ordered += sorted(s for s in by_sector if s not in SECTOR_ORDER)

    return tuple(
        SectorBlock(
            sector=sector,
            cells=tuple(
                sorted(
                    by_sector[sector],
                    key=lambda c: (
                        c.relative_strength is None,
                        -(c.relative_strength or 0.0),
                    ),
                )
            ),
        )
        for sector in ordered
    )


def summarise(blocks: tuple[SectorBlock, ...]) -> dict[str, float | int]:
    """Headline numbers for the dashboard, computed once."""
    cells = [c for block in blocks for c in block.cells]
    rated = [c.relative_strength for c in cells if c.relative_strength is not None]
    return {
        "companies": len(cells),
        "sectors": len(blocks),
        "rated": len(rated),
        "unrated": len(cells) - len(rated),
        "passing": sum(1 for c in cells if c.passed_all),
        "median_rs": statistics.median(rated) if rated else 0.0,
        "beating_index": sum(1 for r in rated if r > 0),
    }
