"""Gemma thinks before she answers (operator 2026-10-19: "give gemma time to
think, maybe more tokens, not to much tho").

Step 1 is plain words with a token cap; step 2 is the usual grammar-constrained
answer with her notes appended. Thinking is never allowed to block a decision.
"""

import json

import httpx
import pytest
import respx

from scanner.advisor import gemma_trader as gt
from scanner.config import settings
from scanner.llm.ollama import LLMError, chat_text


def test_the_think_prompt_asks_for_plain_words_not_the_answer():
    text = gt.think_prompt("BRIEFING")
    assert text.startswith("BRIEFING")
    assert "do not answer" in text.lower()
    assert "plain words" in text


def test_her_notes_are_added_to_the_question():
    text = gt.with_notes("BRIEFING", "MU still strong")
    assert text.startswith("BRIEFING")
    assert "MU still strong" in text
    assert "rules above still apply" in text


def test_long_notes_are_cut_and_empty_notes_change_nothing():
    assert gt.with_notes("BRIEFING", "   ") == "BRIEFING"
    long = gt.with_notes("B", "x" * (gt.MAX_NOTES_CHARS + 500))
    assert long.count("x") == gt.MAX_NOTES_CHARS


@pytest.mark.asyncio
async def test_chat_text_caps_the_tokens_and_sends_no_schema():
    with respx.mock:
        route = respx.post(f"{settings.ollama_host}/api/chat").mock(
            return_value=httpx.Response(200, json={"message": {"content": " notes "}}))
        async with httpx.AsyncClient() as client:
            assert await chat_text("p", client, max_tokens=123) == "notes"
        sent = json.loads(route.calls.last.request.content)
    assert sent["options"]["num_predict"] == 123
    assert "format" not in sent


@pytest.mark.asyncio
async def test_chat_text_raises_on_an_empty_answer():
    with respx.mock:
        respx.post(f"{settings.ollama_host}/api/chat").mock(
            return_value=httpx.Response(200, json={"message": {"content": ""}}))
        async with httpx.AsyncClient() as client:
            with pytest.raises(LLMError):
                await chat_text("p", client, max_tokens=10)
