"""Health monitor tests.

The monitor crashed on its first probe (it passed `checked_at` to classes that
did not accept it) and kept its rejection count in memory only, so no other
process could see it. These pin both fixes.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from scanner.execution import health_monitor as hm
from scanner.execution import safety


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class _FakeClient:
    def __init__(self, tags_json=None, account_json=None, down=False):
        self.tags_json = tags_json
        self.account_json = account_json
        self.down = down

    async def get(self, url, *, timeout=None, params=None, headers=None):
        if self.down:
            raise ConnectionError("unreachable")
        if "/api/tags" in url:
            return _FakeResponse(self.tags_json or {"models": []})
        if "/v2/account" in url:
            return _FakeResponse(self.account_json or {})
        return _FakeResponse({}, 404)


_HEALTHY = dict(
    tags_json={"models": [{"name": "gemma4:12b"}]},
    account_json={"status": "ACTIVE", "equity": "100000",
                  "buying_power": "100000", "cash": "100000"},
)


def _settings(s):
    s.ollama_host = "http://localhost:11434"
    s.triage_model = "gemma4:12b"
    s.alpaca_key_id = "AK"
    s.alpaca_secret_key = "SK"


def _run_all(tmp_path, client):
    with patch("scanner.execution.health_monitor.settings") as s:
        _settings(s)
        monitor = hm.HealthMonitor(str(tmp_path / "KILL"))
        return monitor, asyncio.run(monitor.run_all(client))


def test_probe_does_not_crash_and_reports_healthy(tmp_path):
    _, (model, broker, blocked, reason) = _run_all(tmp_path, _FakeClient(**_HEALTHY))
    assert model.ok
    assert broker.reachable and broker.paper
    assert broker.account_status == "ACTIVE"
    assert not blocked, reason


def test_everything_down_blocks(tmp_path):
    _, (model, broker, blocked, reason) = _run_all(tmp_path, _FakeClient(down=True))
    assert not model.ok
    assert not broker.reachable
    assert blocked
    assert reason


def test_missing_model_blocks(tmp_path):
    client = _FakeClient(tags_json={"models": [{"name": "other"}]},
                         account_json=_HEALTHY["account_json"])
    _, (model, _, blocked, _) = _run_all(tmp_path, client)
    assert model.reachable and not model.model_present
    assert blocked


def test_should_auto_activate_before_any_probe_fails_closed(tmp_path):
    monitor = hm.HealthMonitor(str(tmp_path / "KILL"))
    activate, reason = monitor.should_auto_activate()
    assert activate
    assert reason


def test_rejections_persist_to_disk(tmp_path):
    kill = str(tmp_path / "KILL")
    monitor = hm.HealthMonitor(kill)
    assert monitor.record_rejection() == 1
    assert monitor.record_rejection() == 2
    # Another process (a fresh monitor) sees the same count.
    assert safety.read_consecutive_rejections(kill) == 2
    assert hm.HealthMonitor(kill).record_rejection() == 3
    monitor.reset_rejections()
    assert safety.read_consecutive_rejections(kill) == 0


def test_health_classes_accept_checked_at():
    from datetime import datetime, timezone
    at = datetime(2026, 10, 17, tzinfo=timezone.utc)
    assert hm.ModelHealth(True, True, "m", checked_at=at).checked_at == at
    assert hm.BrokerHealth(True, True, "ACTIVE", checked_at=at).checked_at == at


def test_format_health():
    m = hm.ModelHealth(True, True, "m")
    b = hm.BrokerHealth(False, True, "UNKNOWN")
    assert hm.format_health(m, b) == "models:OK broker:DOWN"
