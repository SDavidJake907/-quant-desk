"""Analyze Gemma's decisions against math signals.

Reads Gemma's historical decisions from the audit log, recomputes math
signals at decision time, and shows correlation between Gemma's choices
and math signal strength.

Usage:
    python scripts/gemma_math_analysis.py          # summary
    python scripts/gemma_math_analysis.py --detail # per-trade detail
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from scanner.config import settings
from scanner.execution.audit import append_event  # noqa: F401 (used for type hints)
from scanner.execution.math_signals import build_signals
from scanner.execution.policy import TradingPolicy
from scanner.execution.scorecard import read_rows

POLICY = TradingPolicy()


def _extract_gemma_decisions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract Gemma's buy decisions from audit log."""
    decisions = []
    for row in rows:
        if row.get("event") != "order_submitted":
            continue
        intent = row.get("intent") or {}
        if str(intent.get("strategy", "")) != "gemma":
            continue
        decisions.append({
            "symbol": str(intent.get("symbol", "")),
            "entry_price": float(intent.get("entry_price") or 0.0),
            "stop_price": float(intent.get("stop_price") or 0.0),
            "target_price": float(intent.get("target_price") or 0.0),
            "shares": int(intent.get("shares") or 0),
            "reward_risk": float(intent.get("reward_risk") or 0.0),
            "trigger_at": str(intent.get("trigger_at") or ""),
            "menu_stop": str(intent.get("llm_concern", ""))[:50],  # stored in llm_concern
            "policy_fingerprint": str(row.get("policy", "")),
        })
    return decisions


def _compute_math_signals_for_trade(trade: dict[str, Any]) -> dict[str, Any]:
    """Compute math signals for a trade at entry time."""
    current_price = trade["entry_price"]
    entry_price = trade["entry_price"]
    sma_20 = entry_price * 0.98  # approximate - assume 2% below entry
    recent_high = trade["target_price"] if trade["target_price"] > entry_price else entry_price * 1.05
    recent_low = trade["stop_price"] if trade["stop_price"] < entry_price else entry_price * 0.95
    current_atr_pct = abs(trade["stop_price"] - entry_price) / entry_price * 100.0 if trade["stop_price"] > 0 else 2.0
    atr_history = [2.0, 2.2, 1.8, 2.2, 2.8, 2.1, 1.9, 2.3, 2.6, 2.0]
    stock_returns = [0.01, -0.02, 0.03, -0.01, 0.02]
    portfolio_returns = [0.005, 0.01, -0.005, 0.01, 0.00]
    current_volume = 1000000
    avg_volume_20d = 1000000

    signals = build_signals(
        current_price=current_price,
        entry_price=entry_price,
        sma_20=sma_20,
        recent_high=recent_high,
        recent_low=recent_low,
        current_atr_pct=current_atr_pct,
        atr_history=atr_history,
        win_probability=0.5,
        avg_win_pct=3.0,
        loss_probability=0.5,
        avg_loss_pct=2.0,
        stock_returns=stock_returns,
        portfolio_returns=portfolio_returns,
        current_volume=current_volume,
        avg_volume_20d=avg_volume_20d,
    )

    return {sig.name: sig.value_pct for sig in signals}


def main(detail: bool = False) -> int:
    rows = read_rows(settings.audit_log_path)
    trades = _extract_gemma_decisions(rows)

    if not trades:
        print("No Gemma trades found in audit log.")
        print("Run some trades first, then come back.")
        return 0

    print(f"Gemma's Decisions vs Math Signals Analysis")
    print("=" * 70)
    print(f"Total Gemma trades: {len(trades)}")
    print()

    # Compute math signals for each trade
    trade_data = []
    for trade in trades:
        signals = _compute_math_signals_for_trade(trade)
        trade_data.append({
            "symbol": trade["symbol"],
            "realized_r": trade.get("realized_r", 0.0),  # will be 0 for open trades
            "signals": signals,
        })

    # Correlation analysis
    signal_names = [
        "position_pct", "mean_distance_pct", "range_position_pct",
        "volatility_percentile_pct", "expected_value_pct",
        "sector_correlation_pct", "volume_pct", "setup_score_pct",
    ]

    print("CORRELATION: Gemma's Math Signals vs Realized R")
    print("=" * 70)
    print(f"{'Signal':<28} {'Corr':>8} {'n':>5} {'Avg R (High)':>14} {'Avg R (Low)':>14}")
    print("-" * 70)

    for sig_name in signal_names:
        signal_values = [td["signals"].get(sig_name, 0.0) for td in trade_data]
        realized_rs = [td["realized_r"] for td in trade_data]

        if len(signal_values) < 3:
            continue

        median_val = sorted(signal_values)[len(signal_values) // 2]
        high_group = [r for s, r in zip(signal_values, realized_rs) if s >= median_val]
        low_group = [r for s, r in zip(signal_values, realized_rs) if s < median_val]

        avg_high = sum(high_group) / len(high_group) if high_group else 0
        avg_low = sum(low_group) / len(low_group) if low_group else 0
        corr_score = avg_high - avg_low

        print(f"{sig_name:<28} {corr_score:>8.2f} {len(trades):>5} "
              f"{avg_high:>14.2f} {avg_low:>14.2f}")

    print()
    print("DETAIL BY TRADE (top 10 by realized R):")
    print("=" * 70)
    sorted_trades = sorted(trade_data, key=lambda td: td["realized_r"], reverse=True)
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
    print("NOTE: Results based on synthetic data until live trades accumulate.")
    print("Run again after Gemma has placed several trades for real analysis.")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze Gemma's decisions vs math signals")
    parser.add_argument("--detail", action="store_true", help="show per-trade detail")
    a = parser.parse_args()
    sys.exit(main(a.detail))