"""Gemma 3 relevance triage.

Job: decide which candidate articles are actually about the target company and
materially matter. Explicitly NOT its job: producing any number.

Two hard guarantees enforced here:
  1. Output is grammar-constrained to the schema below.
  2. Every verdict must cite a span that genuinely occurs in the source text
     AND that actually says something. See `scanner.llm.grounding` — quoting
     the headline back is verbatim, and is not a reason.
"""

from __future__ import annotations

import json

import httpx

from scanner.config import settings
from scanner.llm.grounding import reject_reason
from scanner.llm.ollama import LLMError, chat_json
from scanner.models import NewsItem, TriagedItem, TriageVerdict

CATEGORIES = ("earnings", "guidance", "mna", "legal", "product", "analyst", "macro", "other")
DIRECTIONS = ("positive", "negative", "neutral", "unclear")

# Asking *what role the company plays* discriminates far better than asking
# "is this relevant?". Only `subject` survives — supplier/customer/example are
# exactly the false positives that leaked through the boolean version.
ROLES = ("subject", "supplier", "customer", "competitor", "example", "unrelated")

# Being acquired and doing the acquiring are opposite situations. A TARGET's
# price stops representing its own business and starts tracking the exchange
# ratio, so its chart means nothing. An ACQUIRER buying a small company has
# ordinary news and a chart that still means what it meant yesterday.
# Blocking both — which "category == mna" does — refuses good setups on any
# company active enough to buy things, which skews against exactly the large
# liquid names the universe study preferred.
DEAL_ROLES = ("target", "acquirer", "none")

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "role": {"type": "string", "enum": list(ROLES)},
                    "is_material": {"type": "boolean"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "direction": {"type": "string", "enum": list(DIRECTIONS)},
                    "deal_role": {"type": "string", "enum": list(DEAL_ROLES)},
                    "evidence_quote": {"type": "string"},
                },
                "required": [
                    "index",
                    "role",
                    "is_material",
                    "category",
                    "direction",
                    "deal_role",
                    "evidence_quote",
                ],
            },
        }
    },
    "required": ["verdicts"],
}

_PROMPT = """You are screening financial news for ONE company.

Target company: {company} (ticker {ticker})

For each numbered article, first decide what ROLE {company} plays in it:
- subject     — the article is ABOUT {company}; it is what happened to {company}
- supplier    — {company} supplies, or is supplied by, the company being discussed
- customer    — {company} is named as a customer or partner of the real subject
- competitor  — {company} is named for comparison with the real subject
- example     — {company} is one illustration in a broader story (tariffs, indexes, \
"3 stocks to buy")
- unrelated   — {company} is not meaningfully present

Be strict. If the headline's main claim is about a DIFFERENT company, the role is \
not "subject", even when {company} is named prominently.

Then:
- is_material: could this plausibly move {company}'s stock on its own? Opinion \
pieces, listicles and valuation musings are false.
- category: one of {categories}
- direction: likely effect on {company}, or "unclear"
- deal_role: only for takeover or merger stories. "target" if {company} is the one BEING bought or merged into another; "acquirer" if {company} is the one DOING the buying; "none" for everything else, including all non-deal news. Being bought and buying are opposite situations — read carefully which one this article describes.
- evidence_quote: copy a span VERBATIM from that article's title or summary that \
justifies the role you chose. Copy exactly, character for character. Never \
paraphrase, never invent text, and never quote text that is not there.

Report no prices, percentages or figures of any kind.

Articles:
{articles}

Return exactly one verdict per article, using the article's index."""


def _render(items: tuple[NewsItem, ...]) -> str:
    return "\n\n".join(
        f"[{n}] TITLE: {item.title}\n    SUMMARY: {item.summary or '(none)'}"
        for n, item in enumerate(items)
    )


async def _triage_batch(
    ticker: str, company: str, batch: tuple[NewsItem, ...], client: httpx.AsyncClient
) -> tuple[TriagedItem, ...]:
    prompt = _PROMPT.format(
        ticker=ticker,
        company=company,
        categories=", ".join(CATEGORIES),
        articles=_render(batch),
    )
    raw = await chat_json(prompt, _SCHEMA, client)

    results: list[TriagedItem] = []
    rejections: list[str] = []
    for verdict in raw.get("verdicts", []):
        index = verdict.get("index")
        if not isinstance(index, int) or not 0 <= index < len(batch):
            rejections.append("bad_index")
            continue  # hallucinated index — discard rather than misattribute

        item = batch[index]
        quote = verdict.get("evidence_quote", "")
        rejected = reject_reason(quote, item)
        if rejected is not None:
            rejections.append(rejected)
            continue  # unsupported claim — drop it

        results.append(
            TriagedItem(
                item=item,
                verdict=TriageVerdict(
                    item_id=item.id,
                    role=verdict["role"],
                    is_material=bool(verdict["is_material"]),
                    category=verdict["category"],
                    direction=verdict["direction"],
                    deal_role=str(verdict.get("deal_role", "none")),
                    evidence_quote=quote,
                ),
            )
        )

    if rejections:
        counts = ", ".join(
            f"{reason} x{rejections.count(reason)}" for reason in sorted(set(rejections))
        )
        print(f"  . dropped {len(rejections)} ungrounded verdict(s) for {ticker}: {counts}")
    return tuple(results)


async def triage(
    ticker: str,
    company: str,
    items: tuple[NewsItem, ...],
    client: httpx.AsyncClient,
) -> tuple[TriagedItem, ...]:
    """Triage all items in batches. A failed batch is skipped, not fatal."""
    size = settings.triage_batch_size
    out: list[TriagedItem] = []

    for start in range(0, len(items), size):
        batch = items[start : start + size]
        try:
            out.extend(await _triage_batch(ticker, company, batch, client))
        except (LLMError, KeyError, json.JSONDecodeError) as exc:
            print(f"  ! triage batch {start // size} failed for {ticker}: {exc}")

    return tuple(out)
