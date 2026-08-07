"""Coiled-base screen — the "not yet moved" counterpart to the trend screen.

The trend screen finds what has ALREADY run (LITE at +554%). This one looks
for the opposite profile: an intact uptrend that has gone quiet.

    uptrend intact      50d > 200d, 200d still rising
    right at the highs  within 8% of the 52-week high
    gone quiet          tight 20-day range relative to daily volatility
    volume drying up    50d average volume below its 200d baseline
    NOT parabolic       momentum positive but capped
    not extended        price close to its own 50-day average

The theory is that volatility contracts before it expands, so a tight base
near highs precedes a move. Be clear-eyed: this is practitioner folklore with
plausible mechanics and thin published evidence. It is also the setup most
prone to hindsight bias, because failed bases are invisible on a chart you
only look at after something worked. Backtest before believing it.
"""

from __future__ import annotations

from scanner.features.technicals import Features
from scanner.screens.trend import Candidate, Criterion

# Above this, the stock has already made its move and we are late.
MAX_MOMENTUM = 80.0
MIN_MOMENTUM = 5.0
MAX_PCT_FROM_HIGH = -8.0
MAX_TIGHTNESS = 5.5
MAX_VOLUME_RATIO = 1.0
MAX_EXTENSION_ABOVE_50D = 10.0


def _criteria(f: Features, rs: float | None) -> tuple[Criterion, ...]:
    checks: list[Criterion] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(Criterion(name=name, passed=passed, detail=detail))

    stack_ok = f.sma50 is not None and f.sma200 is not None and f.sma50 > f.sma200
    add(
        "uptrend intact",
        stack_ok and (f.sma200_slope_pct or 0) > 0,
        f"50d {'>' if stack_ok else '<'} 200d, slope "
        f"{f.sma200_slope_pct:+.2f}%" if f.sma200_slope_pct is not None else "n/a",
    )
    add(
        "near 52w high",
        f.pct_from_52w_high > MAX_PCT_FROM_HIGH,
        f"{f.pct_from_52w_high:+.1f}% from high",
    )
    add(
        "range contracted",
        f.range_tightness is not None and f.range_tightness < MAX_TIGHTNESS,
        f"tightness {f.range_tightness:.1f}" if f.range_tightness is not None else "n/a",
    )
    add(
        "volume drying up",
        f.volume_ratio is not None and f.volume_ratio < MAX_VOLUME_RATIO,
        f"50d/200d vol {f.volume_ratio:.2f}" if f.volume_ratio is not None else "n/a",
    )
    add(
        "not parabolic",
        f.momentum_12_1 is not None and MIN_MOMENTUM < f.momentum_12_1 < MAX_MOMENTUM,
        f"12-1 mom {f.momentum_12_1:+.1f}%" if f.momentum_12_1 is not None else "n/a",
    )

    extension = (
        ((f.price - f.sma50) / f.sma50) * 100.0 if f.sma50 else None
    )
    add(
        "not extended",
        extension is not None and extension < MAX_EXTENSION_ABOVE_50D,
        f"{extension:+.1f}% above 50d" if extension is not None else "n/a",
    )
    add("outperforming index", rs is not None and rs > 0,
        f"{rs:+.1f}pp vs benchmark" if rs is not None else "n/a")

    return tuple(checks)


def _score(f: Features, rs: float | None, criteria: tuple[Criterion, ...]) -> float:
    """Rank tighter bases higher — the opposite emphasis to the trend screen."""
    score = sum(6.0 for c in criteria if c.passed)

    # Tightness is the whole thesis, so it carries the most weight.
    if f.range_tightness is not None:
        score += max(0.0, (MAX_TIGHTNESS - f.range_tightness)) * 6.0

    # Closer to the high is better.
    score += max(f.pct_from_52w_high, -10.0) * 1.0

    # Quieter volume is better.
    if f.volume_ratio is not None:
        score += max(0.0, (1.0 - f.volume_ratio)) * 15.0

    # Mild credit for relative strength, capped so it cannot dominate.
    if rs is not None:
        score += max(min(rs, 40.0), -40.0) * 0.15

    return score


def evaluate(f: Features, rs: float | None) -> Candidate:
    criteria = _criteria(f, rs)
    return Candidate(
        symbol=f.symbol,
        features=f,
        relative_strength=rs,
        criteria=criteria,
        score=_score(f, rs, criteria),
    )
