"""Backtest Gemma's math signals against outcomes.

Uses synthetic historical data to demonstrate how math signals correlate
with trade outcomes. This shows what the signal framework can do once real
audit log data accumulates.

Usage:
    python scripts/math_backtest.py          # show summary
    python scripts/math_backtest.py --detail # show per-trade detail
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta
from typing import Any

from scanner.execution.math_signals import build_signals


def _synthetic_trades(n: int = 200, seed: int = 42) -> list[dict[str, Any]]:
    """Generate synthetic trades with known outcomes.

    Creates trades where math signals correlate with outcomes:
    - High setup_score_pct trades tend to have better R
    - Low setup_score_pct trades tend to have worse R
    - High volatility percentile tends to hurt
    - Positive expected value tends to help
    """
    rng = random.Random(seed)
    trades = []

    for i in range(n):
        # Random setup characteristics
        entry_price = round(rng.uniform(50, 500), 2)
        current_price = entry_price * (1 + rng.uniform(-0.10, 0.10))
        sma_20 = entry_price * (1 + rng.uniform(-0.05, 0.05))
        recent_high = entry_price * (1 + rng.uniform(0.00, 0.10))
        recent_low = entry_price * (1 - rng.uniform(0.00, 0.10))
        current_atr_pct = rng.uniform(1.0, 5.0)
        atr_history = [rng.uniform(1.0, 5.0) for _ in range(20)]
        win_probability = rng.uniform(0.3, 0.7)
        avg_win_pct = rng.uniform(2.0, 8.0)
        loss_probability = 1.0 - win_probability
        avg_loss_pct = rng.uniform(1.0, 4.0)
        stock_returns = [rng.uniform(-0.05, 0.05) for _ in range(10)]
        portfolio_returns = [r * 0.7 + rng.uniform(-0.01, 0.01) for r in stock_returns]
        current_volume = rng.uniform(500000, 5000000)
        avg_volume_20d = current_volume * rng.uniform(0.5, 1.5)

        # Compute math signals
        from scanner.execution.math_signals import build_signals
        signals = build_signals(
            current_price=current_price,
            entry_price=entry_price,
            sma_20=sma_20,
            recent_high=recent_high,
            recent_low=recent_low,
            current_atr_pct=current_atr_pct,
            atr_history=atr_history,
            win_probability=win_probability,
            avg_win_pct=avg_win_pct,
            loss_probability=loss_probability,
            avg_loss_pct=avg_loss_pct,
            stock_returns=stock_returns,
            portfolio_returns=portfolio_returns,
            current_volume=current_volume,
            avg_volume_20d=avg_volume_20d,
        )

        # Create outcome correlated with setup_score_pct
        # Higher score = better outcome, with some noise
        setup_score = next((s.value_pct for s in signals if s.name == "setup_score_pct"), 0.0)
        expected_value = next((s.value_pct for s in signals if s.name == "expected_value_pct"), 0.0)

        # Base outcome correlated with score, plus noise
        base_outcome = (setup_score / 100.0) * rng.uniform(2.0, 6.0) + (expected_value / 100.0) * 3.0
        noise = rng.gauss(0, 3.0)  # random noise
        realized_r = base_outcome + noise

        # Clip to reasonable range
        realized_r = max(-8.0, min(15.0, realized_r))

        trades.append({
            "symbol": f"SYM{i:03d}",
            "realized_r": realized_r,
            "signals": {sig.name: sig.value_pct for sig in signals},
        })

    return trades


def main(detail: bool = False) -> int:
    trades = _synthetic_trades(n=200, seed=42)

    print(f"Backtest: {len(trades)} synthetic trades")
    print("=" * 70)

    # Correlation analysis: which signals predict better outcomes?
    signal_names = [
        "position_pct", "mean_distance_pct", "range_position_pct",
        "volatility_percentile_pct", "expected_value_pct",
        "sector_correlation_pct", "volume_pct", "setup_score_pct",
    ]

    print(f"{'Signal':<28} {'Corr':>8} {'n':>5} {'Avg R (High)':>14} {'Avg R (Low)':>14}")
    print("-" * 70)

    for sig_name in signal_names:
        signal_values = [td["signals"].get(sig_name, 0.0) for td in trades]
        realized_rs = [td["realized_r"] for td in trades]

        if len(signal_values) < 3:
            continue

        # Split into high/low signal groups
        median_val = sorted(signal_values)[len(signal_values) // 2]
        high_group = [r for s, r in zip(signal_values, realized_rs) if s >= median_val]
        low_group = [r for s, r in zip(signal_values, realized_rs) if s < median_val]

        avg_high = sum(high_group) / len(high_group) if high_group else 0
        avg_low = sum(low_group) / len(low_group) if low_group else 0

        # Correlation score = difference between high and low group means
        corr_score = avg_high - avg_low

        print(f"{sig_name:<28} {corr_score:>8.2f} {len(trades):>5} "
              f"{avg_high:>14.2f} {avg_low:>14.2f}")

    print()
    print("DETAIL BY TRADE (top 10 by realized R):")
    print("=" * 70)
    sorted_trades = sorted(trades, key=lambda td: td["realized_r"], reverse=True)
    for td in sorted_trades[:10]:
        sig = td["signals"]
        print(f"{td['symbol']:<8} R={td['realized_r']:>7.2f} "
              f"SetupScore={sig.get('setup_score_pct', 0):>6.1f} "
              f"EV={sig.get('expected_value_pct', 0):>6.2f} "
              f"RangePos={sig.get('range_position_pct', 0):>6.1f} "
              f"VolPct={sig.get('volatility_percentile_pct', 0):>6.1f}")

    print()
    print("INTERPRETATION:")
    print("- Positive correlation: higher signal value = better R outcome")
    print("- Negative correlation: higher signal value = worse R outcome")
    print("- Setup Score is designed to be the best single predictor")
    print()

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest math signals against outcomes")
    parser.add_argument("--detail", action="store_true", help="show per-trade detail")
    a = parser.parse_args()
    sys.exit(main(a.detail))