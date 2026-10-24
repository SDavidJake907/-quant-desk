"""A tool-calling loop on Ollama's native tools API (operator 2026-10-23: Gemma tool calling).

The model answers; when it asks for tools, each call is run (`execute`) and its text result is sent
back as a "tool" message; repeat until it answers in plain text or MAX_CALLS calls have been made
(then it is told to answer now). Returns the final text and the transcript of every call.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import httpx

from scanner.config import settings
from scanner.llm.ollama import LLMError, coerce_keep_alive

MAX_CALLS = 6
Execute = Callable[[str, Mapping[str, Any]], Awaitable[str]]


async def _chat(client: httpx.AsyncClient, messages: list[dict], tools: Sequence[dict] | None,
                max_tokens: int, model: str | None) -> dict:
    payload: dict[str, Any] = {
        "model": model or settings.triage_model, "stream": False, "messages": messages,
        "keep_alive": coerce_keep_alive(settings.llm_keep_alive),
        "options": {"temperature": settings.triage_temperature, "num_ctx": settings.triage_num_ctx,
                    "num_predict": max_tokens},
    }
    if tools:
        payload["tools"] = list(tools)
    if settings.llm_think is not None:
        payload["think"] = settings.llm_think
    try:
        r = await client.post(f"{settings.ollama_host}/api/chat", json=payload, timeout=settings.llm_timeout_s)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMError(f"ollama request failed: {exc}") from exc
    return r.json().get("message") or {}


async def run(prompt: str, tools: Sequence[dict], execute: Execute, client: httpx.AsyncClient, *,
              max_calls: int = MAX_CALLS, max_tokens: int = 1500, model: str | None = None,
              chat: Callable[..., Awaitable[dict]] | None = None) -> tuple[str, list[dict]]:
    """(final text, [{"tool", "args", "result"}, ...]). Raises LLMError only if the model cannot be reached."""
    chat = chat or _chat
    messages: list[dict] = [{"role": "user", "content": prompt}]
    calls: list[dict] = []
    while True:
        budget_left = max_calls - len(calls)
        msg = await chat(client, messages, tools if budget_left > 0 else None, max_tokens, model)
        wanted = msg.get("tool_calls") or []
        if not wanted or budget_left <= 0:
            text = str(msg.get("content") or "").strip()
            if text or budget_left > 0:
                return text, calls
            messages.append({"role": "user", "content": "You have used all your lookups. Write your notes now."})
            msg = await chat(client, messages, None, max_tokens, model)
            return str(msg.get("content") or "").strip(), calls
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": wanted})
        for call in wanted[:budget_left]:
            fn = call.get("function") or {}
            name, args = str(fn.get("name", "")), fn.get("arguments") or {}
            result = await execute(name, args)
            calls.append({"tool": name, "args": args, "result": result})
            messages.append({"role": "tool", "content": result, "tool_name": name})
