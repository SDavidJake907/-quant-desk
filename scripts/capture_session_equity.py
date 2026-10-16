"""Capture today's starting equity for the daily-loss guard.

    python scripts/capture_session_equity.py

Reads the current account equity from Alpaca and stores it in
data/session_equity.json keyed by today's date. trade.py then uses it as the
baseline for SCANNER_SESSION_START_EQUITY when that env var is 0, so the
3% daily-loss guard can detect a drawdown that happened before this process
started.

Run once at the start of each trading day (before the first 05:33 AK scan).
Safe to re-run; overwrites today's value.
"""

from __future__ import annotations

import asyncio
import sys

import httpx

from scanner.config import settings
from scanner.execution.alpaca import Alpaca
from scanner.execution.safety import capture_session_equity


async def main() -> int:
    async with httpx.AsyncClient() as client:
        broker = Alpaca(
            settings.alpaca_key_id, settings.alpaca_secret_key, client
        )
        account = await broker.account()
        capture_session_equity(account.equity)
        print(
            f"Session start equity captured: {account.equity:,.2f} "
            f"(status={account.status})"
        )
        return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001
        print(f"failed to capture session equity: {exc}")
        sys.exit(1)
