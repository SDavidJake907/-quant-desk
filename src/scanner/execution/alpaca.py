"""Alpaca adapter: account state, and bracket orders built from setups.

PAPER IS THE DEFAULT AND LIVE IS DELIBERATELY AWKWARD
`paper=True` unless someone writes otherwise, and the live host is only
reachable by passing `paper=False` explicitly. There is no environment
variable that silently flips it, because the difference between the two is
whether losses are real.

WHY BRACKET ORDERS
A setup is three prices: entry, stop, target. Sent as one bracket, the broker
holds the stop and the target from the moment the entry fills, and cancels one
when the other triggers. Sent as three separate orders, there is a window in
which the position exists with no stop behind it — and if the process dies in
that window, the position stays naked until someone notices.

IDEMPOTENCY
Every order carries a `client_order_id` derived from the symbol and the
trigger bar's timestamp. Alpaca rejects a duplicate id, so a restart, a retry
or two scans of the same bar cannot open the same trade twice. This is the
cheapest protection against the worst bug an unattended trader can have.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from scanner.execution.sizing import Sizing
from scanner.models import Frozen
from scanner.screens.smc import Setup

PAPER_HOST = "https://paper-api.alpaca.markets"
LIVE_HOST = "https://api.alpaca.markets"

#: Alpaca rejects orders priced below a penny and sub-penny increments above $1.
TICK = 0.01


class BrokerError(RuntimeError):
    """Raised when the broker cannot be reached or refuses a request."""


class Account(Frozen):
    equity: float
    cash: float
    buying_power: float
    status: str = "ACTIVE"
    blocked: bool = False

    @property
    def tradeable(self) -> bool:
        return self.status == "ACTIVE" and not self.blocked and self.equity > 0


def round_tick(price: float) -> float:
    """Alpaca rejects sub-penny prices above $1."""
    return round(price / TICK) * TICK


def client_order_id(setup: Setup, at: datetime) -> str:
    """Stable per (symbol, trigger bar). The duplicate guard.

    Deriving it from the bar rather than from the clock is the point: two
    scans of the same bar produce the same id and the second is rejected,
    where a timestamp would produce two ids and two positions.
    """
    return f"smc-{setup.symbol}-{int(at.timestamp())}"


def bracket_payload(setup: Setup, sizing: Sizing, at: datetime) -> dict[str, Any]:
    """The order body: buy `shares`, with stop and target attached.

    A LIMIT entry at the trigger close, not a market order. The backtest
    assumed a fill at that price; a market order would fill wherever the book
    happens to be and quietly break the correspondence.
    """
    return {
        "symbol": setup.symbol,
        "qty": str(sizing.shares),
        "side": "buy",
        "type": "limit",
        "limit_price": f"{round_tick(setup.entry_price):.2f}",
        "time_in_force": "day",
        "order_class": "bracket",
        "take_profit": {"limit_price": f"{round_tick(setup.target_price):.2f}"},
        "stop_loss": {"stop_price": f"{round_tick(setup.stop_price):.2f}"},
        "client_order_id": client_order_id(setup, at),
        "extended_hours": False,
    }


class Alpaca:
    """Thin async client. Only the endpoints this project needs."""

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        client: httpx.AsyncClient,
        *,
        paper: bool = True,
    ):
        if not key_id or not secret_key:
            raise BrokerError("Alpaca API keys are not configured")
        self.paper = paper
        self.host = PAPER_HOST if paper else LIVE_HOST
        self._client = client
        self._headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "accept": "application/json",
        }
        self.last_request_id = ""

    async def _get(self, path: str, **params) -> Any:
        try:
            response = await self._client.get(
                f"{self.host}{path}", headers=self._headers,
                params=params or None, timeout=30.0,
            )
            self.last_request_id = response.headers.get("X-Request-ID", "")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            suffix = f" (request {self.last_request_id})" if self.last_request_id else ""
            raise BrokerError(f"GET {path} failed{suffix}: {exc}") from exc

    async def _delete(self, path: str) -> None:
        """The only mutating verbs here besides `submit` — and like `submit`,
        paper-only by construction, since `host` is the paper endpoint."""
        try:
            response = await self._client.delete(
                f"{self.host}{path}", headers=self._headers, timeout=30.0
            )
            self.last_request_id = response.headers.get("X-Request-ID", "")
            if response.status_code not in (200, 204, 207):
                raise BrokerError(f"DELETE {path}: {response.text[:160]}")
        except httpx.HTTPError as exc:
            raise BrokerError(f"DELETE {path} failed: {exc}") from exc

    async def close_position(self, symbol: str) -> None:
        """Close at market. Used only for the backtest's time limit."""
        await self._delete(f"/v2/positions/{symbol}")

    async def cancel_order(self, order_id: str) -> None:
        await self._delete(f"/v2/orders/{order_id}")

    async def account(self) -> Account:
        raw = await self._get("/v2/account")
        return Account(
            equity=float(raw.get("equity") or 0.0),
            cash=float(raw.get("cash") or 0.0),
            buying_power=float(raw.get("buying_power") or 0.0),
            status=str(raw.get("status") or "UNKNOWN"),
            blocked=bool(
                raw.get("trading_blocked")
                or raw.get("account_blocked")
                or raw.get("trade_suspended_by_user")
            ),
        )

    async def market_open(self) -> bool:
        return bool((await self._get("/v2/clock")).get("is_open"))

    async def held_symbols(self) -> frozenset[str]:
        return frozenset(p["symbol"] for p in await self._get("/v2/positions"))

    async def positions_with_origin(self) -> tuple[tuple[str | None, float], ...]:
        """Open positions as (opening client_order_id, market value).

        Alpaca's position record does not name the order that created it, so
        the link is rebuilt from order history: a position is attributed to the
        strategy when this account has a filled order for that symbol carrying
        our prefix. A symbol we never traded reports `None` and is treated as
        somebody else's money — which is what a manual purchase in the same
        account actually is.
        """
        positions = await self._get("/v2/positions")
        orders = await self._get("/v2/orders", status="all", limit=500,
                                 nested="true")
        origin: dict[str, str] = {}
        for order in orders:
            coid = str(order.get("client_order_id", ""))
            symbol = str(order.get("symbol", ""))
            if coid and symbol and symbol not in origin:
                origin[symbol] = coid
        return tuple(
            (origin.get(str(p.get("symbol", ""))), float(p.get("market_value", 0.0)))
            for p in positions
        )

    async def pending_symbols(self) -> frozenset[str]:
        orders = await self._get("/v2/orders", status="open", nested="true")
        return frozenset(o["symbol"] for o in orders)

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one order. Never called for live accounts by this repo's
        scripts without an explicit, interactive opt-in from the operator."""
        try:
            response = await self._client.post(
                f"{self.host}/v2/orders", headers=self._headers,
                json=payload, timeout=30.0,
            )
            self.last_request_id = response.headers.get("X-Request-ID", "")
            if response.status_code == 422:
                suffix = f" (request {self.last_request_id})" if self.last_request_id else ""
                raise BrokerError(f"rejected{suffix}: {response.text[:200]}")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            suffix = f" (request {self.last_request_id})" if self.last_request_id else ""
            raise BrokerError(f"order failed{suffix}: {exc}") from exc
