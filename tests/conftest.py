"""Make `scripts/` importable so the supervisor can be tested directly.

`scripts` is not a package and the files are entry points, but a watchdog that
is only exercised by running it is a watchdog nobody exercises.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("scripts_watchdog", _ROOT / "scripts" / "watchdog.py")


class FakeBroker:
    """Captures submitted orders without hitting Alpaca."""

    def __init__(self):
        self.submitted: list[dict] = []

    async def submit(self, payload: dict) -> dict:
        self.submitted.append(dict(payload))
        return {"id": f"order-{len(self.submitted)}"}


import pytest


@pytest.fixture
def fake_broker():
    return FakeBroker()
