"""Monitor the Alpaca paper account without changing it.

    python scripts/monitor.py                 # one read-only check
    python scripts/monitor.py --interval 60   # repeat until Ctrl+C

Exit status is 0 when no critical finding exists and 2 otherwise. The monitor
never submits, cancels, or closes anything.
"""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx

from scanner.config import settings
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.guards import RiskLimits
from scanner.execution.policy import TradingPolicy
from scanner.execution.safety import format_report, inspect, kill_switch_active
from positions import load_intents

POLICY = TradingPolicy()


async def check_once(as_json: bool) -> bool:
    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        account = await broker._get("/v2/account")
        positions = await broker._get("/v2/positions")
        orders = await broker._get("/v2/orders", status="all", nested="true", limit=200, direction="desc")
        market_open = await broker.market_open()

    report = inspect(
        account, positions, orders, load_intents(settings.audit_log_path),
        RiskLimits(max_positions=POLICY.max_positions,
                   max_daily_loss_pct=POLICY.max_daily_loss_pct),
        market_open=market_open,
        kill_switch=kill_switch_active(settings.kill_switch_path),
    )
    if as_json:
        print(json.dumps({
            "healthy": report.healthy,
            "findings": [f.__dict__ for f in report.findings],
            "positions": len(positions),
            "orders": len(orders),
            "market_open": market_open,
        }, indent=2))
    else:
        print(format_report(report))
        print(f"positions={len(positions)} orders={len(orders)} market_open={market_open}")
    return report.healthy


async def main(interval: int, as_json: bool) -> None:
    while True:
        healthy = await check_once(as_json)
        if interval <= 0:
            if not healthy:
                raise SystemExit(2)
            return
        await asyncio.sleep(interval)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=0, help="repeat every N seconds")
    p.add_argument("--json", action="store_true", dest="as_json")
    a = p.parse_args()
    try:
        asyncio.run(main(a.interval, a.as_json))
    except KeyboardInterrupt:
        pass
    except BrokerError as exc:
        raise SystemExit(f"broker unavailable: {exc}")
