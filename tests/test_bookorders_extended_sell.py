"""After hours a sell needs a fresh price. Without one, the stop must stay in place."""

import pytest

from scanner.execution import bookorders
from scanner.execution.policy import TradingPolicy


class FakeBroker:
    def __init__(self):
        self.cancelled: list[str] = []
        self.submitted: list[dict] = []

    async def cancel_order(self, order_id: str) -> None:
        self.cancelled.append(order_id)

    async def _get(self, path, **params):
        return [{"symbol": "NUE", "qty": "38", "qty_available": "38"}]

    async def submit(self, payload):
        self.submitted.append(payload)
        return payload


STOP = [{"id": "stop-1", "symbol": "NUE", "side": "sell", "type": "stop"}]


@pytest.fixture(autouse=True)
def quiet_log(monkeypatch):
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)


async def test_no_fresh_price_keeps_the_stop(monkeypatch):
    async def no_price(*a, **k):
        return None
    monkeypatch.setattr(bookorders.extended, "price", no_price)
    broker = FakeBroker()
    ok = await bookorders.sell(broker, "NUE", STOP, strategy="gemma", reason="t",
                               policy=TradingPolicy(), session="after", client=object())
    assert not ok
    assert broker.cancelled == []          # the stop was NOT cancelled
    assert broker.submitted == []


async def test_fresh_price_cancels_then_sells_with_a_limit(monkeypatch):
    async def price(*a, **k):
        return 258.0
    monkeypatch.setattr(bookorders.extended, "price", price)
    broker = FakeBroker()
    ok = await bookorders.sell(broker, "NUE", STOP, strategy="gemma", reason="t",
                               policy=TradingPolicy(), session="after", client=object())
    assert ok
    assert broker.cancelled == ["stop-1"]
    assert broker.submitted[0]["type"] == "limit" and broker.submitted[0]["qty"] == "38"
