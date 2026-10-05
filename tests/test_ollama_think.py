"""`think` is sent only when configured.

Gemma 4 thinks by default: ~22s and ~8k characters of reasoning per triage
batch, and on a cold call the answer came back empty -- a skipped batch, which
the news gate treats as nothing to refuse. With think=false the same batch took
~2s with the schema intact (measured 2026-10-04, Ollama 0.35.1). Unset, nothing
is sent, which keeps Gemma 3 and the thinking-template bug the docstring warns
about exactly as they were.
"""

import json

import httpx
import pytest
import respx

from scanner.config import settings
from scanner.llm.ollama import chat_json

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


async def _sent_payload(monkeypatch, think):
    monkeypatch.setattr(settings, "llm_think", think)
    with respx.mock:
        route = respx.post(f"{settings.ollama_host}/api/chat").mock(
            return_value=httpx.Response(200, json={"message": {"content": '{"ok": true}'}}))
        async with httpx.AsyncClient() as client:
            assert await chat_json("p", SCHEMA, client) == {"ok": True}
        return json.loads(route.calls.last.request.content)


@pytest.mark.asyncio
async def test_unset_sends_no_think_field(monkeypatch):
    assert "think" not in await _sent_payload(monkeypatch, None)


@pytest.mark.asyncio
async def test_false_is_sent_when_configured(monkeypatch):
    assert (await _sent_payload(monkeypatch, False))["think"] is False
