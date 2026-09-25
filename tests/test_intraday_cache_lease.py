"""A cache hit must not renew its own lease.

THE BUG THIS PINS
`fetch_intraday` read the cache through `fetch_payload`, which returns a
payload whether it fetched or not, and then called `cache_payload`
unconditionally — rewriting the cache with the bytes just read out of it and
stamping a fresh `cached_at`. A stale payload renewed its own lease on every
read and could outlive its TTL indefinitely.

Measured live on 2026-09-25: a cache 29 minutes old served bars three buckets
behind what the provider was returning, and its timestamp came back 0.0
minutes old afterwards.

Why it mattered rather than being untidy: the TTL was one full bar, which is
also the scan cadence, so a scan 30 minutes later sat exactly on the expiry
boundary. With `fresh_bars = 2`, one bar of lag eats half the life of every
setup.
"""

from __future__ import annotations

import json
import time

import pytest

from scanner.features import bars as bars_module
from scanner.features.intraday import fetch_intraday


class _Client:
    """Counts real requests. Never actually reaches the network."""

    def __init__(self, payload):
        self.payload = payload
        self.requests = 0

    async def get(self, *args, **kwargs):
        self.requests += 1
        raise AssertionError("network should not be reached in this test")


def _plant(tmp_path, monkeypatch, payload, age_seconds):
    monkeypatch.setattr(bars_module, "CACHE_DIR", tmp_path)
    path = bars_module._cache_path("TEST", "60d", "30m")
    path.write_text(json.dumps({
        "cached_on": "2026-09-25",
        "cached_at": time.time() - age_seconds,
        "payload": payload,
    }), encoding="utf-8")
    return path


def _payload():
    start = 1_790_000_000
    stamps = [start + i * 1800 for i in range(40)]
    n = len(stamps)
    return {"chart": {"result": [{
        "timestamp": stamps,
        "meta": {"exchangeTimezoneName": "America/New_York"},
        "indicators": {"quote": [{
            "open": [10.0] * n, "high": [11.0] * n,
            "low": [9.0] * n, "close": [10.5] * n, "volume": [1000] * n,
        }]},
    }]}}


@pytest.mark.asyncio
async def test_a_cache_hit_does_not_reset_its_own_expiry(tmp_path, monkeypatch):
    """THE BUG. Reading a cache must not extend how long it lives."""
    path = _plant(tmp_path, monkeypatch, _payload(), age_seconds=60)
    before = json.loads(path.read_text(encoding="utf-8"))["cached_at"]

    await fetch_intraday("TEST", _Client(_payload()), minutes=30, range_="60d")

    after = json.loads(path.read_text(encoding="utf-8"))["cached_at"]
    assert after == before, "a cache hit rewrote the cache and renewed its lease"


@pytest.mark.asyncio
async def test_the_ttl_is_shorter_than_the_scan_cadence(tmp_path, monkeypatch):
    """Scans run every 30 minutes on 30-minute bars. A TTL of one full bar put
    every scan exactly on the expiry boundary, so freshness came down to
    sub-second jitter."""
    _plant(tmp_path, monkeypatch, _payload(), age_seconds=16 * 60)
    client = _Client(_payload())
    with pytest.raises(AssertionError):
        # 16 minutes is past half a bar, so this MUST try to refetch.
        await fetch_intraday("TEST", client, minutes=30, range_="60d")


@pytest.mark.asyncio
async def test_a_young_cache_is_still_reused(tmp_path, monkeypatch):
    """The cache still has to work, or every scan refetches 501 symbols."""
    _plant(tmp_path, monkeypatch, _payload(), age_seconds=60)
    client = _Client(_payload())
    result = await fetch_intraday("TEST", client, minutes=30, range_="60d")
    assert client.requests == 0
    assert len(result) > 0
