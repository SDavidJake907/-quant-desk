"""The position review asks the model nothing while the market is shut.

It ran every scan, seven days a week: 14 reviews on Saturday 2026-10-03 and 14
on Sunday, of a position whose price could not move. Record-only, so the calls
decided nothing -- they only held the GPU.
"""

import asyncio
import importlib.util
from pathlib import Path

import pytest


def _load():
    spec = importlib.util.spec_from_file_location(
        "review_positions_t", Path(__file__).parent.parent / "scripts" / "review_positions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBroker:
    def __init__(self, is_open):
        self.is_open, self.asked = is_open, []

    async def market_open(self):
        return self.is_open

    async def _get(self, path, **_):
        self.asked.append(path)
        return [{"symbol": "INTC", "qty": "169", "avg_entry_price": "118.8",
                 "current_price": "118.0", "unrealized_pl": "-145"}]


@pytest.mark.parametrize("is_open", [False])
def test_closed_market_means_no_review(monkeypatch, is_open):
    module = _load()
    broker = FakeBroker(is_open)
    monkeypatch.setattr(module, "Alpaca", lambda *a, **k: broker)

    async def no_model(*a, **k):
        raise AssertionError("the model was asked while the market was closed")

    monkeypatch.setattr(module, "chat_json", no_model)
    assert asyncio.run(module.main("gemma4:12b")) == 0
    assert broker.asked == []        # did not even read positions
