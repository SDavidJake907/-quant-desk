"""Thin Ollama adapter.

Kept deliberately narrow so the runtime can be swapped for llama.cpp or vLLM
later without touching callers.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from scanner.config import settings


class LLMError(RuntimeError):
    """Raised when the model call fails or returns unusable output."""


def coerce_keep_alive(value: str | int | float) -> str | int:
    """Ollama takes a duration string ("5m") or a bare number of seconds.

    It does NOT take a numeric string: `-1` means "keep loaded forever" but
    `"-1"` is parsed as a duration and fails with
    `time: missing unit in duration "-1"`. Because settings are strings, an
    env value of -1 arrived quoted and every triage call returned 400 — the
    model sat loaded in VRAM doing nothing, which looks exactly like working.
    """
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


async def chat_json(
    prompt: str,
    schema: dict[str, Any],
    client: httpx.AsyncClient,
    *,
    model: str | None = None,
    keep_alive: str | None = None,
) -> dict[str, Any]:
    """Call the model with grammar-constrained JSON output.

    `schema` is enforced by Ollama's sampler, so malformed JSON is structurally
    impossible rather than something we hope for and regex around.

    `keep_alive` controls how long the model stays in VRAM afterwards. Pass
    "0" when benchmarking two models on one GPU — the default holds both
    resident, and a 12B beside a 9B does not fit in 16GB alongside the
    embedding model.

    Note on `think`: it is deliberately NOT sent. Ollama has a reported bug
    where `think=false` combined with `format` silently drops the schema
    constraint on models with thinking templates (Nemotron, Qwen3). Gemma 3
    has no thinking template and is unaffected, but any model swapped in here
    must be checked against that before its output is trusted.
    """
    payload = {
        "model": model or settings.triage_model,
        "stream": False,
        "keep_alive": coerce_keep_alive(keep_alive or settings.llm_keep_alive),
        "format": schema,
        "options": {
            "temperature": settings.triage_temperature,
            "num_ctx": settings.triage_num_ctx,
        },
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        response = await client.post(
            f"{settings.ollama_host}/api/chat",
            json=payload,
            timeout=settings.llm_timeout_s,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMError(f"ollama request failed: {exc}") from exc

    content = response.json().get("message", {}).get("content", "")
    if not content.strip():
        raise LLMError("model returned empty content")

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"model returned non-JSON despite schema: {exc}") from exc


async def embed(texts: list[str], client: httpx.AsyncClient) -> list[list[float]]:
    """Embed a batch of strings with the local embedding model."""
    if not texts:
        return []

    try:
        response = await client.post(
            f"{settings.ollama_host}/api/embed",
            json={"model": settings.embed_model, "input": texts},
            timeout=settings.llm_timeout_s,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMError(f"embedding request failed: {exc}") from exc

    embeddings = response.json().get("embeddings")
    if not embeddings or len(embeddings) != len(texts):
        raise LLMError("embedding count did not match input count")
    return embeddings
