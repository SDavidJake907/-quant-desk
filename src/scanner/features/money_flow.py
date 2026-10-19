"""Where the money is moving: sector dollar-volume share and per-stock flow.

DESCRIPTIVE, NOT A TESTED SIGNAL (2026-10-18). Accumulation (rising OBV while
basing) failed twice here, insider buying twice, short ratio once (WORKLIST,
TRIALS #5, #6, #43). What had NOT been tried: a sector's SHARE of the market's
dollar volume, and requiring flow on top of 12-1 momentum. The morning leaders
report uses this to pick a second list of 4 beside the plain one, and both are
graded forward (scanner.ops.leaders_grade) before anything trades on it.

RULES, FIXED BEFORE THE FIRST GRADE
Sector (members of the S&P 500 + MidCap 400, by GICS sector):
  share_5 / share_20   the sector's share of all members' dollar volume over
                       the last 5 / 20 sessions; INFLOW when share_5 > share_20
                       AND the sector's median 5-day return is above zero
Stock, four checks, one point each:
  sector_inflow        its sector is INFLOW
  dollar_surge         mean dollar volume, last 5 sessions >= 1.2x the 20 before
  up_volume            up-day volume is more than half of the last 10 sessions'
  obv_rising           on-balance volume above its level 10 sessions ago
  (closes_high, mean close-location over 5 sessions > 0, is reported but not
  scored: it is the price side of the same bar the other checks read)
A stock is FLOWING with 3 or more points.
PURE.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping

from scanner.features.bars import Bars
from scanner.models import Frozen

SHORT = 5
LONG = 20
SURGE = 1.2
UP_WINDOW = 10
OBV_WINDOW = 10
FLOWING_POINTS = 3


class SectorFlow(Frozen):
    sector: str
    share_5: float
    share_20: float
    median_5d: float
    members: int
    dollars_5d: float             # mean dollar volume per session, last 5
    dollars_20d: float            # mean dollar volume per session, last 20

    @property
    def share_change(self) -> float:
        """Relative change of the sector's dollar-volume share, in %."""
        return (self.share_5 / self.share_20 - 1) * 100 if self.share_20 else 0.0

    @property
    def inflow(self) -> bool:
        return self.share_5 > self.share_20 and self.median_5d > 0


class StockFlow(Frozen):
    symbol: str
    sector_inflow: bool
    dollar_surge: float | None
    up_volume_share: float | None
    obv_rising: bool
    close_location: float | None

    @property
    def points(self) -> int:
        return sum((self.sector_inflow,
                    self.dollar_surge is not None and self.dollar_surge >= SURGE,
                    self.up_volume_share is not None and self.up_volume_share > 0.5,
                    self.obv_rising))

    @property
    def flowing(self) -> bool:
        return self.points >= FLOWING_POINTS


def _dollar_volume(bars: Bars, start: int, end: int | None = None) -> float:
    """Sum of close x volume over bars[start:end] (negative indexes allowed)."""
    return sum(c * v for c, v in zip(bars.closes[start:end], bars.volumes[start:end]))


def _ret(closes: tuple[float, ...], n: int) -> float | None:
    return (closes[-1] / closes[-n - 1] - 1) * 100 if len(closes) > n and closes[-n - 1] else None


def sector_flows(daily: Mapping[str, Bars], sector: Mapping[str, str]) -> dict[str, SectorFlow]:
    """Each sector's dollar-volume share (5 vs 20 sessions) and median 5-day return."""
    dv5: dict[str, float] = defaultdict(float)
    dv20: dict[str, float] = defaultdict(float)
    rets: dict[str, list[float]] = defaultdict(list)
    for symbol, bars in daily.items():
        if len(bars) <= LONG or symbol not in sector:
            continue
        name = sector[symbol]
        dv5[name] += _dollar_volume(bars, -SHORT)
        dv20[name] += _dollar_volume(bars, -LONG)
        r = _ret(bars.closes, SHORT)
        if r is not None:
            rets[name].append(r)
    total5, total20 = sum(dv5.values()), sum(dv20.values())
    if not total5 or not total20:
        return {}
    return {name: SectorFlow(sector=name, share_5=dv5[name] / total5 * 100,
                             share_20=dv20[name] / total20 * 100,
                             median_5d=statistics.median(rets[name]) if rets[name] else 0.0,
                             members=len(rets[name]),
                             dollars_5d=dv5[name] / SHORT, dollars_20d=dv20[name] / LONG)
            for name in dv5}


def stock_flow(symbol: str, bars: Bars, sector_inflow: bool) -> StockFlow:
    closes, vols = bars.closes, bars.volumes
    n = len(closes)
    surge = None
    if n >= SHORT + LONG:
        base = _dollar_volume(bars, -(SHORT + LONG), -SHORT) / LONG
        surge = (_dollar_volume(bars, -SHORT) / SHORT) / base if base else None
    up_share = None
    if n > UP_WINDOW:
        window = range(n - UP_WINDOW, n)
        total = sum(vols[i] for i in window)
        up = sum(vols[i] for i in window if closes[i] > closes[i - 1])
        up_share = up / total if total else None
    obv_rising = False
    if n > OBV_WINDOW:
        # OBV now minus OBV OBV_WINDOW sessions ago = signed volume over the window
        delta = sum(vols[i] if closes[i] > closes[i - 1] else -vols[i] if closes[i] < closes[i - 1] else 0
                    for i in range(n - OBV_WINDOW, n))
        obv_rising = delta > 0
    clv = None
    if n >= SHORT:
        locs = [((c - lo) - (hi - c)) / (hi - lo)
                for c, hi, lo in zip(closes[-SHORT:], bars.highs[-SHORT:], bars.lows[-SHORT:])
                if hi > lo]
        clv = sum(locs) / len(locs) if locs else None
    return StockFlow(symbol=symbol, sector_inflow=sector_inflow, dollar_surge=surge,
                     up_volume_share=up_share, obv_rising=obv_rising, close_location=clv)
