"""Mathematical context signals for Gemma's decisions.

All functions are pure: same inputs always produce same outputs. None reach the
broker, none place orders, none raise on bad input — they degrade gracefully so
Gemma always has *some* context, never none.

Every function returns (value_pct, confidence, explanation):
  value_pct:   percentage (e.g., +4.2% gain, 78th percentile, -1.5% below mean)
  confidence:  0.0 to 1.0 — how much to trust this number
  explanation: one sentence for Gemma's prompt
"""

from __future__ import annotations

from statistics import fmean
from typing import Optional

from scanner.models import Frozen


class MathSignal(Frozen):
    """One mathematical signal for Gemma's decision context."""

    name: str              # e.g., "position_pct", "volatility_percentile"
    value_pct: float       # percentage value (can be negative)
    confidence: float      # 0.0 to 1.0
    explanation: str       # one sentence for Gemma's prompt


# ---------------------------------------------------------------------------
# Position & Return Math
# ---------------------------------------------------------------------------


def position_pct(current_price: float, entry_price: float) -> MathSignal:
    """Unrealized gain/loss as percentage of entry.

    Tells Gemma how much she's already up or down on this position.
    """
    if entry_price <= 0:
        return MathSignal(
            name="position_pct",
            value_pct=0.0,
            confidence=0.0,
            explanation="No entry price available — position P&L unknown.",
        )
    value = (current_price - entry_price) / entry_price * 100.0
    return MathSignal(
        name="position_pct",
        value_pct=round(value, 2),
        confidence=1.0,
        explanation=(
            f"Position is {value:+.2f}% from entry "
            f"({'up' if value >= 0 else 'down'} {'%.2f' % abs(value)}%)"
        ),
    )


def mean_distance_pct(current_price: float, sma_20: float) -> MathSignal:
    """Distance from 20-day simple moving average as percentage.

    Positive = above average (extended), negative = below average (pulled back).
    Helps Gemma judge if entry is extended or resting.
    """
    if sma_20 <= 0:
        return MathSignal(
            name="mean_distance_pct",
            value_pct=0.0,
            confidence=0.0,
            explanation="No 20-day average available — distance unknown.",
        )
    value = (current_price - sma_20) / sma_20 * 100.0
    side = "above" if value >= 0 else "below"
    return MathSignal(
        name="mean_distance_pct",
        value_pct=round(value, 2),
        confidence=0.9,
        explanation=(
            f"Price is {value:+.2f}% from its 20-day average "
            f"({side} average)"
        ),
    )


# ---------------------------------------------------------------------------
# Range & Relative Position Math
# ---------------------------------------------------------------------------


def range_position_pct(
    current_price: float,
    recent_high: float,
    recent_low: float,
) -> MathSignal:
    """Where price sits within its recent range as percentage.

    0% = at recent low, 50% = middle, 100% = at recent high.
    Helps Gemma judge if entry is near the top (extended) or bottom (shaking out).
    """
    if recent_high <= recent_low:
        return MathSignal(
            name="range_position_pct",
            value_pct=50.0,
            confidence=0.0,
            explanation="No valid recent range — position unknown.",
        )
    value = (current_price - recent_low) / (recent_high - recent_low) * 100.0
    value = max(0.0, min(100.0, value))  # clamp to 0-100
    if value >= 80:
        location = "near the top of its recent range"
    elif value <= 20:
        location = "near the bottom of its recent range"
    else:
        location = "in the middle of its recent range"
    return MathSignal(
        name="range_position_pct",
        value_pct=round(value, 1),
        confidence=0.85,
        explanation=f"Price is {value:.1f}% of its recent range ({location})",
    )


# ---------------------------------------------------------------------------
# Volatility Math
# ---------------------------------------------------------------------------


def volatility_percentile_pct(
    current_atr_pct: float,
    atr_history: list[float],
) -> MathSignal:
    """Percentile rank of current ATR% vs last 60 days.

    Tells Gemma if current volatility is unusually high (stops should be wider)
    or unusually low (stops can be tighter).
    """
    if not atr_history or len(atr_history) < 10:
        return MathSignal(
            name="volatility_percentile_pct",
            value_pct=50.0,
            confidence=0.0,
            explanation="Not enough ATR history — volatility percentile unknown.",
        )
    history = sorted(atr_history)
    n = len(history)
    below = sum(1 for h in history if h < current_atr_pct)
    value = below / n * 100.0
    if value >= 75:
        regime = "high volatility — stops should be wider"
    elif value <= 25:
        regime = "low volatility — stops can be tighter"
    else:
        regime = "normal volatility"
    return MathSignal(
        name="volatility_percentile_pct",
        value_pct=round(value, 1),
        confidence=0.85,
        explanation=f"Current volatility is at the {value:.0f}th percentile "
                    f"of the last {n} days ({regime})",
    )


# ---------------------------------------------------------------------------
# Expectancy Math
# ---------------------------------------------------------------------------


def expected_value_pct(
    win_probability: float,
    avg_win_pct: float,
    loss_probability: float,
    avg_loss_pct: float,
) -> MathSignal:
    """Expected value of the trade as percentage of entry.

    EV = P(win) × avg_win − P(loss) × avg_loss
    Positive = expected to make money, negative = expected to lose.
    """
    win_prob = max(0.0, min(1.0, win_probability))
    loss_prob = max(0.0, min(1.0, loss_probability))
    # Normalize so they sum to 1 if both provided
    total = win_prob + loss_prob
    if total > 0:
        win_prob /= total
        loss_prob /= total

    ev = win_prob * avg_win_pct - loss_prob * avg_loss_pct
    confidence = 0.7  # based on sample size of historical setups
    if ev > 0:
        side = "positive"
    elif ev < 0:
        side = "negative"
    else:
        side = "neutral"
    return MathSignal(
        name="expected_value_pct",
        value_pct=round(ev, 2),
        confidence=confidence,
        explanation=f"Expected value is {ev:+.2f}% ({side})",
    )


# ---------------------------------------------------------------------------
# Correlation Math
# ---------------------------------------------------------------------------


def sector_correlation_pct(
    stock_returns: list[float],
    portfolio_returns: list[float],
) -> MathSignal:
    """Pearson correlation between stock returns and portfolio returns as %.

    Tells Gemma if adding this stock concentrates her book in one direction.
    100% = moves exactly with her book, 0% = independent, -100% = opposite.
    """
    if len(stock_returns) < 5 or len(portfolio_returns) < 5:
        return MathSignal(
            name="sector_correlation_pct",
            value_pct=0.0,
            confidence=0.0,
            explanation="Not enough return history — correlation unknown.",
        )
    n = min(len(stock_returns), len(portfolio_returns))
    sx = stock_returns[:n]
    sy = portfolio_returns[:n]
    mx = fmean(sx)
    my = fmean(sy)
    cov = sum((a - mx) * (b - my) for a, b in zip(sx, sy)) / n
    sx_std = (sum((a - mx) ** 2 for a in sx) / n) ** 0.5
    sy_std = (sum((b - my) ** 2 for b in sy) / n) ** 0.5
    if sx_std == 0 or sy_std == 0:
        return MathSignal(
            name="sector_correlation_pct",
            value_pct=0.0,
            confidence=0.0,
            explanation="No return variation — correlation undefined.",
        )
    corr = cov / (sx_std * sy_std)
    corr = max(-1.0, min(1.0, corr))  # clamp
    value = corr * 100.0
    if abs(value) >= 70:
        strength = "highly correlated"
    elif abs(value) >= 40:
        strength = "moderately correlated"
    else:
        strength = "weakly correlated"
    return MathSignal(
        name="sector_correlation_pct",
        value_pct=round(value, 1),
        confidence=0.8,
        explanation=f"Correlation with your book: {value:+.1f}% ({strength})",
    )


# ---------------------------------------------------------------------------
# Volume Math
# ---------------------------------------------------------------------------


def volume_pct(current_volume: float, avg_volume_20d: float) -> MathSignal:
    """Current volume as percentage of 20-day average.

    >100% = above average (conviction), <100% = below average (quiet).
    """
    if avg_volume_20d <= 0:
        return MathSignal(
            name="volume_pct",
            value_pct=100.0,
            confidence=0.0,
            explanation="No average volume available — volume context unknown.",
        )
    value = current_volume / avg_volume_20d * 100.0
    if value >= 150:
        side = "high volume"
    elif value <= 70:
        side = "low volume"
    else:
        side = "normal volume"
    return MathSignal(
        name="volume_pct",
        value_pct=round(value, 1),
        confidence=0.9,
        explanation=f"Volume is {value:.0f}% of its 20-day average ({side})",
    )


# ---------------------------------------------------------------------------
# Composite Score
# ---------------------------------------------------------------------------


def setup_score_pct(
    position_pct_val: float,
    range_position_pct_val: float,
    volatility_percentile_pct_val: float,
    expected_value_pct_val: float,
    volume_pct_val: float,
) -> MathSignal:
    """Composite setup score from -100 to +100.

    Combines position, range position, volatility, expected value, and volume
    into a single score. Positive = good setup, negative = avoid.
    """
    # Weighted score: expected value matters most, then range position, volume
    score = (
        expected_value_pct_val * 0.40 +
        range_position_pct_val * 0.20 +
        volume_pct_val * 0.15 +
        (50 - abs(position_pct_val)) * 0.15 +  # closer to entry = better
        (50 - abs(volatility_percentile_pct_val - 50)) * 0.10  # mid volatility = best
    )
    score = max(-100.0, min(100.0, score))
    if score >= 50:
        quality = "strong setup"
    elif score >= 20:
        quality = "decent setup"
    elif score >= -20:
        quality = "neutral setup"
    else:
        quality = "poor setup"
    return MathSignal(
        name="setup_score_pct",
        value_pct=round(score, 1),
        confidence=0.75,
        explanation=f"Composite setup score: {score:+.0f}/100 ({quality})",
    )


# ---------------------------------------------------------------------------
# Convenience: build all signals for Gemma
# ---------------------------------------------------------------------------


def build_signals(
    current_price: float,
    entry_price: float,
    sma_20: float,
    recent_high: float,
    recent_low: float,
    current_atr_pct: float,
    atr_history: list[float],
    win_probability: float,
    avg_win_pct: float,
    loss_probability: float,
    avg_loss_pct: float,
    stock_returns: list[float],
    portfolio_returns: list[float],
    current_volume: float,
    avg_volume_20d: float,
) -> list[MathSignal]:
    """Build all math signals for one candidate symbol.

    Returns a list of MathSignal objects Gemma can read in her prompt.
    """
    signals: list[MathSignal] = []
    signals.append(position_pct(current_price, entry_price))
    signals.append(mean_distance_pct(current_price, sma_20))
    signals.append(range_position_pct(current_price, recent_high, recent_low))
    signals.append(volatility_percentile_pct(current_atr_pct, atr_history))
    signals.append(expected_value_pct(win_probability, avg_win_pct,
                                      loss_probability, avg_loss_pct))
    signals.append(sector_correlation_pct(stock_returns, portfolio_returns))
    signals.append(volume_pct(current_volume, avg_volume_20d))
    signals.append(setup_score_pct(
        position_pct(current_price, entry_price).value_pct,
        range_position_pct(current_price, recent_high, recent_low).value_pct,
        volatility_percentile_pct(current_atr_pct, atr_history).value_pct,
        expected_value_pct(win_probability, avg_win_pct,
                           loss_probability, avg_loss_pct).value_pct,
        volume_pct(current_volume, avg_volume_20d).value_pct,
    ))
    return signals
