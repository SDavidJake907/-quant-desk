"""Stop-loss logic beyond raw ATR multiples.

ATR-only stops are simple but rigid: they don't account for market structure
(swing lows, support), don't trail as price moves in your favor, and don't
adjust for volatility regimes. This module adds layers on top of the ATR base
stop so exits are smarter without being opaque.

STOP HIERARCHY (each layer can only move the stop UP, never down):
1. ATR base stop: entry - (atr_multiple * atr_pct * entry)  [always applied]
2. Structure stop: max(atr_stop, recent_swing_low)           [optional, via use_structure]
3. Regime adjustment: stop * regime_multiplier               [optional, via regime_adjust]
4. Trailing stop: chandelier exit from highest high since entry [optional, via trail_pct]

All values are prices. Higher = safer (further from entry), lower = tighter risk.
"""

from __future__ import annotations

from math import floor
from typing import Optional

from scanner.features.structure import Swing, swings
from scanner.features.intraday import IntradayBars
from scanner.models import Frozen


class StopPlan(Frozen):
    """The complete stop plan for a position.

    stop_price:    The price at which the full position exits (or remaining shares
                   after partial tier sells).
    atr_stop:      The raw ATR-based stop before any adjustments.
    structure_stop: The swing-low-based stop (or None if structure not used).
    trail_stop:    The trailing chandelier stop (or None if trailing not active).
    regime_mult:   The volatility regime multiplier applied.
    reason:        Human-readable explanation of how the stop was derived.
    """

    stop_price: float
    atr_stop: float
    structure_stop: Optional[float]
    trail_stop: Optional[float]
    regime_mult: float
    reason: str


class StopTier(Frozen):
    """One partial-stop tier: price and share fraction.

    When price reaches target_price, pct_of_shares of the remaining position
    is sold, and the stop moves to the next tier's stop (or stays).
    """

    target_price: float
    pct_of_shares: float  # 0.0 to 1.0 — share of REMAINING position to sell
    stop_price: float     # stop level after this tier fills
    label: str


def _swing_lows(bars: IntradayBars, k: int = 2, lookback: int = 60) -> list[Swing]:
    """Confirmed swing lows from bar data, most recent first."""
    all_swings = swings(bars.highs, bars.lows, k)
    visible = [s for s in all_swings if s.kind == "low"]
    # Filter to lookback window
    if len(bars) > lookback:
        visible = [s for s in visible if s.index >= len(bars) - lookback]
    return sorted(visible, key=lambda s: s.index, reverse=True)


def structure_stop(
    bars: IntradayBars,
    entry_price: float,
    atr_stop: float,
    k: int = 2,
    lookback: int = 60,
    max_slippage_pct: float = 0.5,
) -> Optional[float]:
    """Use the most recent confirmed swing low as a stop reference.

    The swing low must be BELOW the ATR stop (otherwise ATR is already
    tighter). We also cap how far below the entry it can go — a swing low
    10% below entry isn't a stop, it's a catastrophe.

    Returns None if no usable swing low exists.
    """
    lows = _swing_lows(bars, k=k, lookback=lookback)
    if not lows:
        return None

    # Most recent swing low that is below the ATR stop (structure is deeper)
    # but not so far below that it's meaningless (> max_slippage_pct below entry)
    for swing in lows:
        swing_price = swing.price
        if swing_price < atr_stop and swing_price > entry_price * (1 - max_slippage_pct / 100):
            return swing_price

    return None


def regime_multiplier(atr_pct: float, atr_pct_history: list[float]) -> float:
    """Volatility regime multiplier.

    If current ATR% is in the top quartile of recent history, widen stops
    (volatility is high, stops need room). If in the bottom quartile, tighten
    slightly (volatility is low, stops can be closer).

    Returns a multiplier applied to the stop DISTANCE from entry (not the price).
    A multiplier of 1.25 means the stop distance is 25% wider.
    """
    if len(atr_pct_history) < 20:
        return 1.0

    sorted_hist = sorted(atr_pct_history)
    n = len(sorted_hist)
    p75 = sorted_hist[int(n * 0.75)]
    p25 = sorted_hist[int(n * 0.25)]

    if atr_pct >= p75:
        return 1.25  # High vol: wider stops
    elif atr_pct <= p25:
        return 0.90  # Low vol: slightly tighter
    return 1.0  # Normal vol: unchanged


def chandelier_exit(
    bars: IntradayBars,
    entry_price: float,
    atr_multiple: float = 3.0,
    lookback: int = 22,
) -> Optional[float]:
    """Chandelier exit: highest high since entry minus (atr_multiple * ATR).

    Trails the stop up as price rises. Only activates once price has moved
    in your favor. Returns None if price hasn't moved enough to trail.

    The chandelier is: highest_high - (atr_multiple * atr)
    If current price is below the chandelier, exit.
    """
    if len(bars.closes) < lookback:
        return None

    # Highest high since entry (or lookback window, whichever is shorter)
    recent_highs = bars.highs[-lookback:]
    highest_high = max(recent_highs)

    # ATR over lookback
    true_ranges = []
    for i in range(max(1, len(bars) - lookback), len(bars)):
        prior_close = bars.closes[i - 1]
        tr = max(
            bars.highs[i] - bars.lows[i],
            abs(bars.highs[i] - prior_close),
            abs(bars.lows[i] - prior_close),
        )
        true_ranges.append(tr)

    if not true_ranges:
        return None

    atr = sum(true_ranges) / len(true_ranges)
    chandelier = highest_high - (atr_multiple * atr)

    # Only trail if the chandelier is ABOVE the entry (we're in profit)
    if chandelier > entry_price:
        return chandelier

    return None


def build_stop_plan(
    bars: IntradayBars,
    entry_price: float,
    atr_pct: float,
    atr_multiple: float,
    *,
    use_structure: bool = False,
    regime_adjust: bool = False,
    atr_history: Optional[list[float]] = None,
    trail_pct: Optional[float] = None,
    trail_lookback: int = 22,
) -> StopPlan:
    """Build a complete stop plan.

    Layers are applied in order: ATR base -> structure -> regime -> trail.
    Each layer can only move the stop UP (safer), never down.
    """
    # 1. ATR base stop
    atr_stop = round_tick(entry_price * (1 - atr_multiple * atr_pct / 100))
    if atr_stop <= 0:
        atr_stop = entry_price * 0.5  # floor at 50% below entry

    # 2. Structure stop (swing low)
    structure_stop_price: Optional[float] = None
    if use_structure:
        structure_stop_price = structure_stop(bars, entry_price, atr_stop)

    # Use the deeper (higher price) of ATR stop and structure stop
    current_stop = atr_stop
    if structure_stop_price is not None and structure_stop_price > current_stop:
        current_stop = structure_stop_price

    # 3. Regime adjustment
    regime_mult = 1.0
    if regime_adjust and atr_history:
        regime_mult = regime_multiplier(atr_pct, atr_history)
        # Apply multiplier to the DISTANCE from entry, not the price
        stop_distance = entry_price - current_stop
        current_stop = entry_price - (stop_distance * regime_mult)
        # Don't go below ATR stop
        current_stop = max(current_stop, atr_stop)

    # 4. Trailing stop (chandelier)
    trail_stop_price: Optional[float] = None
    if trail_pct is not None:
        # trail_pct is the ATR multiple for the chandelier
        trail_stop_price = chandelier_exit(bars, entry_price, atr_multiple=trail_pct, lookback=trail_lookback)
        if trail_stop_price is not None and trail_stop_price > current_stop:
            current_stop = trail_stop_price

    # Build reason string
    parts = [f"ATR {atr_multiple}x"]
    if structure_stop_price is not None:
        parts.append(f"struct {structure_stop_price:.2f}")
    if regime_mult != 1.0:
        parts.append(f"regime {regime_mult:.2f}x")
    if trail_stop_price is not None:
        parts.append(f"trail {trail_stop_price:.2f}")

    return StopPlan(
        stop_price=round_tick(current_stop),
        atr_stop=round_tick(atr_stop),
        structure_stop=structure_stop_price,
        trail_stop=trail_stop_price,
        regime_mult=regime_mult,
        reason=" + ".join(parts),
    )


def round_tick(price: float, tick: float = 0.01) -> float:
    """Round to nearest tick (penny for stocks > $1)."""
    return round(price / tick) * tick


def stop_tiers(
    entry_price: float,
    stop_price: float,
    atr_pct: float,
    atr_multiple: float,
    tier_config: tuple[tuple[float, float], ...] = (),
) -> tuple[StopTier, ...]:
    """Build take-profit-linked stop tiers.

    Each tier: when price hits target_price, sell pct_of_shares of the
    REMAINING position, and move the stop to tier_stop_price.

    tier_config: tuple of (target_price_pct_above_entry, pct_to_sell, stop_pct_below_entry)
    Example: ((0.02, 0.50, -0.01), (0.04, 0.25, 0.00), (0.06, 0.25, 0.02))
    = sell 50% at +2%, move stop to -1%; sell 25% at +4%, move stop to breakeven;
        sell 25% at +6%, move stop to +2%
    """
    tiers: list[StopTier] = []
    remaining = 1.0

    for target_pct, sell_pct, stop_pct in tier_config:
        target_price = round_tick(entry_price * (1 + target_pct))
        tier_stop = round_tick(entry_price * (1 + stop_pct))
        shares_to_sell = sell_pct  # fraction of remaining

        tiers.append(StopTier(
            target_price=target_price,
            pct_of_shares=shares_to_sell,
            stop_price=tier_stop,
            label=f"{target_pct:+.0%}",
        ))
        remaining -= sell_pct

    return tuple(tiers)