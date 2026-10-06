"""The monitoring math and its coded form.

SWING (a bracket trade: entry, stop, target)
  R   R now: (price - entry) / (entry - stop)
  P   fair chance of target first with NO edge: (price - stop) / (target - stop).
      On a driftless price the value of holding is exactly where it stands, so
      exiting early gains nothing unless information says otherwise.
  MF  best point since entry, in R      MA  worst point since entry, in R
  C   cushion to stop in 30-minute ATRs
  G   cushion to stop in DAILY ATRs -- under ~1, an overnight gap can go
      through the stop and lose more than 1R
  B   bars held / bar limit             F   sessions until the weekend flatten

MOMENTUM (a ranked holding, no stop)
  K   rank percentile in the universe now (100 = top)
  W   weight % of the book              S   share % of the book in its sector
  T   S&P 500 gap to its 210-day average, % (negative = the next tranche goes to cash)
  L   20-day average dollar volume, $M  M   12-1 momentum, %

No tickers, no dollar amounts: a line is safe on a lock screen.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

LEGEND = __doc__.split("No tickers")[0].strip()

_SIGNED = {"R", "MF", "MA", "T", "M"}
_PLACES = {"R": 2, "P": 2, "MF": 2, "MA": 2, "C": 1, "G": 1, "K": 1, "W": 1, "S": 0,
           "T": 1, "L": 0, "M": 0}


def swing(*, entry: float, stop: float, target: float, price: float,
          highs: Sequence[float], lows: Sequence[float], atr30: float, atr_daily: float,
          bars_held: int, max_bars: int, sessions_to_flatten: int) -> dict:
    risk = entry - stop
    span = target - stop
    return {
        "R": (price - entry) / risk,
        "P": min(1.0, max(0.0, (price - stop) / span)) if span > 0 else 0.0,
        "MF": (max(highs, default=price) - entry) / risk,
        "MA": (min(lows, default=price) - entry) / risk,
        "C": (price - stop) / atr30 if atr30 else 0.0,
        "G": (price - stop) / atr_daily if atr_daily else 0.0,
        "B": f"{bars_held}/{max_bars}",
        "F": sessions_to_flatten,
    }


def momentum(*, rank: int, universe: int, weight_pct: float, sector_share_pct: float,
             trend_gap_pct: float, dollar_volume: float, mom_12_1: float) -> dict:
    return {"K": (1 - (rank - 1) / universe) * 100, "W": weight_pct, "S": sector_share_pct,
            "T": trend_gap_pct, "L": dollar_volume / 1e6, "M": mom_12_1}


def heat(positions: Iterable[tuple[float, float, float]], *, equity: float) -> float:
    """Money at risk to the stops, % of equity: sum (price - stop) x shares."""
    return sum(max(price - stop, 0.0) * qty for price, stop, qty in positions) / equity * 100


def render(values: Mapping) -> str:
    parts = []
    for key, value in values.items():
        if isinstance(value, str) or isinstance(value, int) and key not in _PLACES:
            parts.append(f"{key}{value}")
            continue
        places = _PLACES.get(key, 2)
        text = f"{value:+.{places}f}" if key in _SIGNED else f"{value:.{places}f}"
        parts.append(f"{key}{text}")
    return " ".join(parts)
