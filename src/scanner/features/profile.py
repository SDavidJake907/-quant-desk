"""Fixed-range volume profile: where the volume actually traded.

WHAT IT PRODUCES
    PoC   point of control — the price bin that traded the most volume
    VAH   value area high  — top of the band holding `value_area` of volume
    VAL   value area low   — bottom of that band

THE APPROXIMATION, STATED UP FRONT
A true volume profile needs volume at each price, which means tick or
level-2 data. Yahoo gives one volume figure per bar and nothing about where
inside the bar it traded. So each bar's volume is spread UNIFORMLY across its
high-low range.

That is an assumption, not a measurement, and it matters:

  - A bar that opened at its low, spiked, and closed back down really traded
    most of its volume near the low. Spread uniformly, it reports as if the
    spike held as much interest as the base.
  - Wide bars smear volume across many bins; narrow bars concentrate it. So
    the profile is biased toward calling quiet, tight areas the PoC.
  - Our PoC will therefore NOT match TradingView's, which uses finer intrabar
    data. Close, usually. Identical, never.

Use it as a description of roughly where trade concentrated. Do not use it as
a precise level, and do not put a stop one tick beyond it as though it were
measured — it is interpolated.
"""

from __future__ import annotations

from scanner.features.intraday import IntradayBars
from scanner.models import Frozen

#: Share of volume the value area contains. 70% is the Market Profile
#: convention (roughly one standard deviation), not a derived optimum.
DEFAULT_VALUE_AREA = 0.70

#: Price bins across the range. More bins means finer resolution and noisier
#: PoC placement; fewer means coarser but steadier.
DEFAULT_BINS = 50


class ProfileError(RuntimeError):
    """Raised when a profile cannot be built from the given range."""


class VolumeProfile(Frozen):
    """Volume distributed across price bins for one fixed range of bars."""

    symbol: str
    start_index: int
    end_index: int
    low: float
    high: float
    bins: tuple[float, ...]
    poc_index: int
    value_area_low: float
    value_area_high: float

    @property
    def bin_size(self) -> float:
        return (self.high - self.low) / len(self.bins) if self.bins else 0.0

    @property
    def poc_price(self) -> float:
        """Centre of the heaviest bin. A band reported as a number — the
        precision is in the reporting, not in the data."""
        return self.low + self.bin_size * (self.poc_index + 0.5)

    @property
    def total_volume(self) -> float:
        return sum(self.bins)

    @property
    def value_area_volume(self) -> float:
        lo = int((self.value_area_low - self.low) / self.bin_size) if self.bin_size else 0
        hi = int((self.value_area_high - self.low) / self.bin_size) if self.bin_size else 0
        return sum(self.bins[lo : hi + 1])

    def contains(self, price: float) -> bool:
        """Whether a price sits inside the value area."""
        return self.value_area_low <= price <= self.value_area_high


def _distribute(profile: list[float], low: float, high: float, volume: float,
                range_low: float, bin_size: float, bins: int) -> None:
    """Spread one bar's volume evenly over the bins its range touches."""
    first = max(0, min(bins - 1, int((low - range_low) / bin_size)))
    last = max(0, min(bins - 1, int((high - range_low) / bin_size)))
    touched = last - first + 1
    share = volume / touched
    for i in range(first, last + 1):
        profile[i] += share


def build(
    bars: IntradayBars,
    start_index: int,
    end_index: int,
    *,
    bins: int = DEFAULT_BINS,
    value_area: float = DEFAULT_VALUE_AREA,
) -> VolumeProfile:
    """Profile the bars in [start_index, end_index] inclusive.

    Point-in-time by construction: it reads a closed range of bars and cannot
    see past `end_index`.
    """
    if bins < 1:
        raise ProfileError("a profile needs at least one bin")
    if not 0 < value_area <= 1:
        raise ProfileError("value area must be a share between 0 and 1")
    start_index = max(0, start_index)
    end_index = min(len(bars) - 1, end_index)
    if end_index <= start_index:
        raise ProfileError("profile range must cover at least two bars")

    lows = bars.lows[start_index : end_index + 1]
    highs = bars.highs[start_index : end_index + 1]
    volumes = bars.volumes[start_index : end_index + 1]
    range_low, range_high = min(lows), max(highs)
    if range_high <= range_low:
        raise ProfileError("profile range has no price extent")

    bin_size = (range_high - range_low) / bins
    buckets = [0.0] * bins
    for low, high, volume in zip(lows, highs, volumes):
        _distribute(buckets, low, high, volume or 0.0, range_low, bin_size, bins)

    total = sum(buckets)
    if total <= 0:
        raise ProfileError("profile range carries no volume")

    poc = max(range(bins), key=lambda i: buckets[i])

    # Grow outward from the PoC, always taking the heavier neighbour, until
    # the band holds `value_area` of the volume. Single-bin steps; the classic
    # Market Profile method compares pairs, which shifts the edges slightly.
    lo = hi = poc
    covered = buckets[poc]
    target = total * value_area
    while covered < target and (lo > 0 or hi < bins - 1):
        below = buckets[lo - 1] if lo > 0 else -1.0
        above = buckets[hi + 1] if hi < bins - 1 else -1.0
        if above >= below:
            hi += 1
            covered += buckets[hi]
        else:
            lo -= 1
            covered += buckets[lo]

    return VolumeProfile(
        symbol=bars.symbol,
        start_index=start_index,
        end_index=end_index,
        low=range_low,
        high=range_high,
        bins=tuple(buckets),
        poc_index=poc,
        value_area_low=range_low + bin_size * lo,
        value_area_high=range_low + bin_size * (hi + 1),
    )
