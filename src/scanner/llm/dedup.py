"""Embedding-based near-duplicate removal.

The same story reaches us reworded across outlets. Comparing embeddings is
faster, cheaper and far more consistent than asking an LLM "are these the
same?" — so the LLM never sees this problem at all.
"""

from __future__ import annotations

import math

import httpx

from scanner.config import settings
from scanner.llm.ollama import embed
from scanner.models import NewsItem


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def deduplicate(
    items: tuple[NewsItem, ...], client: httpx.AsyncClient
) -> tuple[NewsItem, ...]:
    """Return a new tuple with near-duplicates dropped, earliest item winning.

    Falls back to returning the input unchanged if embedding is unavailable —
    a degraded feed beats a dead pipeline.
    """
    if len(items) < 2:
        return items

    vectors = await embed([f"{i.title} {i.summary}".strip() for i in items], client)

    kept: list[NewsItem] = []
    kept_vectors: list[list[float]] = []
    for item, vector in zip(items, vectors):
        if any(
            _cosine(vector, seen) >= settings.dedup_threshold for seen in kept_vectors
        ):
            continue
        kept.append(item)
        kept_vectors.append(vector)

    return tuple(kept)
