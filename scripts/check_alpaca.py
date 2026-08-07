"""Read-only Alpaca paper-account connectivity check.

This script never submits an order. Run it after adding paper credentials.
"""

from __future__ import annotations

import asyncio

import httpx

from scanner.config import settings
from scanner.execution.alpaca import Alpaca, BrokerError


async def main() -> None:
    async with httpx.AsyncClient() as client:
        try:
            broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
            account = await broker.account()
            is_open = await broker.market_open()
            held = await broker.held_symbols()
            pending = await broker.pending_symbols()
        except BrokerError as exc:
            print(f"Alpaca paper connection failed: {exc}")
            raise SystemExit(1) from exc

    print("Alpaca paper connection: OK")
    print(f"account status: {account.status}")
    print(f"tradeable: {account.tradeable}")
    print(f"equity: {account.equity:,.2f}")
    print(f"buying power: {account.buying_power:,.2f}")
    print(f"market open: {is_open}")
    print(f"positions: {len(held)}")
    print(f"working orders: {len(pending)}")
    print(f"request id: {broker.last_request_id or 'not returned'}")
    print("No order was submitted.")


if __name__ == "__main__":
    asyncio.run(main())
