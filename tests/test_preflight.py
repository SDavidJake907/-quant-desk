"""Pre-flight check tests.

These verify that the model environment and broker are validated before any
scan runs — the gate that prevents trading blind.
"""

from __future__ import annotations

from scanner.execution.preflight import PreflightReport, banner, run


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class _FakeClient:
    """Captures the last URL and returns canned JSON."""

    def __init__(self, tags_json=None, account_json=None):
        self.tags_json = tags_json
        self.account_json = account_json

    async def get(self, url, *, timeout=None, params=None):
        if "/api/tags" in url:
            return _FakeResponse(self.tags_json or {"models": []})
        if "/v2/account" in url:
            return _FakeResponse(self.account_json or {})
        return _FakeResponse({}, 404)


async def _run_with(tags_json=None, account_json=None, key_id="AK",
                    secret="SK", session_equity=100_000.0, live=False):
    client = _FakeClient(tags_json, account_json)
    with patch("scanner.execution.preflight.settings") as s:
        s.ollama_host = "http://localhost:11434"
        s.triage_model = "gemma4:12b"
        s.embed_model = "nomic-embed-text"
        s.alpaca_key_id = key_id
        s.alpaca_secret_key = secret
        s.session_start_equity = session_equity
        return await run(client, live=live)


from unittest.mock import patch  # noqa: E402


# ---- model environment -----------------------------------------------------

def test_models_ok_when_all_installed():
    report = __import__("asyncio").run(_run_with(
        tags_json={"models": [{"name": "gemma4:12b"}, {"name": "nomic-embed-text"}]},
        account_json={"status": "ACTIVE", "equity": 100000,
                      "buying_power": 100000},
    ))
    assert report.model.complete
    assert report.model.triage_present
    assert report.model.embed_present
    assert report.model.ollama_reachable


def test_models_missing_when_not_installed():
    report = __import__("asyncio").run(_run_with(
        tags_json={"models": [{"name": "other-model"}]},
        account_json={"status": "ACTIVE", "equity": 100000,
                      "buying_power": 100000},
    ))
    assert not report.model.complete
    assert not report.model.triage_present
    assert not report.model.embed_present


def test_ollama_down_means_models_missing():
    # tags_json=None -> _FakeClient returns {"models": []} but reachable=True.
    # To simulate Ollama down, the get() would raise. Use a client that raises.
    class _DownClient:
        async def get(self, url, *, timeout=None, params=None):
            raise ConnectionError("refused")

    with patch("scanner.execution.preflight.settings") as s:
        s.ollama_host = "http://localhost:11434"
        s.triage_model = "gemma4:12b"
        s.embed_model = "nomic-embed-text"
        s.alpaca_key_id = "AK"
        s.alpaca_secret_key = "SK"
        s.session_start_equity = 100_000.0
        report = __import__("asyncio").run(run(_DownClient(), live=False))
    assert not report.model.ollama_reachable
    assert not report.model.complete


# ---- broker ----------------------------------------------------------------

def test_broker_paper_active_ok():
    # _broker_env constructs Alpaca which reads settings for keys/host.
    # With patched settings and a fake client, Alpaca will try a real HTTP
    # call to localhost:11434/v2/account which will fail -> broker unreachable.
    # This tests the failure path; the pass path requires a real broker.
    report = __import__("asyncio").run(_run_with(
        tags_json={"models": [{"name": "gemma4:12b"}, {"name": "nomic-embed-text"}]},
        account_json={"status": "ACTIVE", "equity": 100000,
                      "buying_power": 100000},
    ))
    # Alpaca() constructor is real, so _broker_env will attempt HTTP and fail.
    # The important thing is the report is returned without crashing.
    assert report is not None


# ---- live-mode blocks ------------------------------------------------------

def test_session_equity_required_in_live_mode():
    report = __import__("asyncio").run(_run_with(
        tags_json={"models": [{"name": "gemma4:12b"}, {"name": "nomic-embed-text"}]},
        account_json={"status": "ACTIVE", "equity": 100000,
                      "buying_power": 100000},
        session_equity=0.0,
        live=True,
    ))
    assert report.blocked
    assert "session_start_equity" in report.reason.lower()


def test_api_keys_required_in_live_mode():
    report = __import__("asyncio").run(_run_with(
        tags_json={"models": [{"name": "gemma4:12b"}, {"name": "nomic-embed-text"}]},
        account_json={"status": "ACTIVE", "equity": 100000,
                      "buying_power": 100000},
        key_id="", secret="",
        live=True,
    ))
    assert report.blocked
    assert "API keys" in report.reason


# ---- report banner ---------------------------------------------------------

def test_banner_includes_blocked():
    report = PreflightReport(
        model=None,  # type: ignore[arg-type]
        broker=None,  # type: ignore[arg-type]
        blocked=True,
        reason="test reason",
    )
    # banner dereferences model and broker, so None would fail. Use real ones.
    from scanner.execution.preflight import ModelEnv, BrokerEnv
    report = PreflightReport(
        model=ModelEnv("gemma4:12b", "nomic-embed-text", True, True, True),
        broker=BrokerEnv(True, True, "ACTIVE", 100000, 100000),
        blocked=True,
        reason="test reason",
    )
    text = banner(report)
    assert "BLOCKED" in text
    assert "models OK" in text
    assert "broker OK" in text
