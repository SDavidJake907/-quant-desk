"""Gemma's opinion on which setups look best — recorded, not obeyed.

WHY THE MODEL GETS THIS JOB AND NOT THE OTHER ONE
Finding the setup is arithmetic over bars: swing pivots, a sweep, a break.
A model does that worse, differently on each run, and untestably. Choosing
between five setups that have already passed every deterministic test is a
different question, and part of it is genuinely textual — is this company
about to be reorganised, is the move already explained by something that has
nothing to do with structure.

So the split is by competence: **price is arithmetic, context is language.**

WHAT IT IS AND IS NOT ALLOWED
The schema has no numeric field, so the model cannot emit an entry, a stop, a
target or a size. It never sees prices, because a model shown prices starts
doing arithmetic badly instead of reading. It ranks by conviction and must
quote the text it is reasoning from.

AND IT DOES NOT DECIDE — YET
Its ranking is written to the audit log beside the deterministic one and
compared later. Acting on it now would make the strategy unbacktestable and
throw away the only out-of-sample evidence this project has. If, after a few
hundred paper trades, the model's top-ranked setups have outperformed the
deterministic order, that will be a measured fact rather than a hope.
"""

from __future__ import annotations

import json

import httpx

from scanner.llm.grounding import occurs_verbatim
from scanner.llm.ollama import LLMError, chat_json
from scanner.models import Frozen, NewsItem

CONVICTIONS = ("strong", "neutral", "weak")

# What is actually moving the stock. The deterministic layer cannot tell these
# apart — it sees a sweep and a break either way — but the distinction is
# mechanical, not cosmetic: a sweep that happened because the whole sector sold
# off on an oil print is beta, not a company-specific liquidity grab, and the
# pattern the backtest measured was the latter.
DRIVERS = ("company", "sector", "market", "none")

_SCHEMA = {
    "type": "object",
    "properties": {
        "opinions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "conviction": {"type": "string", "enum": list(CONVICTIONS)},
                    "concern": {"type": "string"},
                    "driver": {"type": "string", "enum": list(DRIVERS)},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["symbol", "conviction", "concern", "driver",
                             "evidence_quote"],
            },
        }
    },
    "required": ["opinions"],
}

_PROMPT = """You are reviewing companies that have already passed a mechanical \
price screen. The screen has decided these are valid setups. You are NOT \
judging the chart — you cannot see it, and price is not your job.

Your only question for each company: is there anything in its recent news that \
makes it a WORSE candidate than the others here?

Each company is listed with its SECTOR and how that sector is performing against the market, so you can judge whether its news is its own or its sector's.

- strong   nothing in the news works against it; the move is unexplained by news
- neutral  ordinary news; nothing that changes what the price means
- weak     something that makes price structure unreliable — a merger or \
acquisition, litigation, a restructuring, a guidance withdrawal, or a move \
that is entirely explained by a one-off announcement

driver: what is moving this stock, judged from the headlines —
  "company"  something specific to this business
  "sector"   something affecting its whole sector (an oil price move, a rate
             decision for banks, a drug-pricing rule for health care)
  "market"   a broad risk-on or risk-off move affecting everything
  "none"     no news explains it
This matters: a price move caused by a sector-wide event is not the same event as one caused by the company, even when the chart looks identical.

concern: name the specific worry in a few words, or "none".
evidence_quote: copy a span VERBATIM from that company's headlines that \
supports your call. Copy exactly. If there is no news, use an empty string and \
say conviction "neutral".

Report no prices, percentages, targets or figures of any kind. You are ranking \
by context only.

Companies:
{companies}

Return exactly one opinion per company, using its symbol."""


class SetupOpinion(Frozen):
    """The model's read on one candidate. No numbers, by schema."""

    symbol: str
    conviction: str = "neutral"
    driver: str = "none"
    concern: str = ""
    evidence_quote: str = ""
    grounded: bool = True

    @property
    def rank_key(self) -> int:
        """Lower sorts first."""
        return {"strong": 0, "neutral": 1, "weak": 2}.get(self.conviction, 1)


def _render(
    candidates: dict[str, tuple[NewsItem, ...]],
    sectors: dict[str, str] | None = None,
    sector_rs: dict[str, float] | None = None,
) -> str:
    """One block per company: its sector, how that sector stands, its news.

    The sector standing is arithmetic we already have — the model is told it
    rather than asked to work it out, so it can judge whether a headline
    explains this company or its whole sector.
    """
    blocks = []
    for symbol, items in candidates.items():
        note = ""
        sector = (sectors or {}).get(symbol, "")
        if sector:
            median = (sector_rs or {}).get(sector)
            if median is None:
                note = f"  (sector: {sector})"
            else:
                standing = "leading" if median > 0 else "lagging"
                note = f"  (sector: {sector} — {standing} the market)"
        headlines = "\n".join(f"    - {item.title}" for item in items[:5]) or "    (no recent news)"
        blocks.append(f"[{symbol}]{note}\n{headlines}")
    return "\n\n".join(blocks)


async def rank(
    candidates: dict[str, tuple[NewsItem, ...]],
    client: httpx.AsyncClient,
    sectors: dict[str, str] | None = None,
    sector_rs: dict[str, float] | None = None,
) -> tuple[SetupOpinion, ...]:
    """Rank candidates by news context. Never raises.

    A model outage returns neutral opinions rather than an error, because this
    is advisory: nothing downstream may be blocked by the model being down.
    """
    if not candidates:
        return ()
    neutral = tuple(SetupOpinion(symbol=s) for s in candidates)
    try:
        raw = await chat_json(
            _PROMPT.format(companies=_render(candidates, sectors, sector_rs)),
            _SCHEMA, client,
        )
    except (LLMError, json.JSONDecodeError):
        return neutral

    opinions: list[SetupOpinion] = []
    for entry in raw.get("opinions", []):
        symbol = str(entry.get("symbol", "")).upper()
        if symbol not in candidates:
            continue  # hallucinated ticker — discard rather than misattribute
        quote = str(entry.get("evidence_quote", ""))
        grounded = not quote.strip() or any(
            occurs_verbatim(quote, item) for item in candidates[symbol]
        )
        opinions.append(SetupOpinion(
            symbol=symbol,
            conviction=str(entry.get("conviction", "neutral")),
            driver=str(entry.get("driver", "none")),
            concern=str(entry.get("concern", ""))[:120],
            evidence_quote=quote if grounded else "",
            grounded=grounded,
        ))

    seen = {o.symbol for o in opinions}
    opinions.extend(o for o in neutral if o.symbol not in seen)
    return tuple(opinions)


def order(opinions: tuple[SetupOpinion, ...]) -> tuple[str, ...]:
    """Symbols in the model's preferred order.

    Ungrounded opinions fall back to neutral: a conviction the model could not
    evidence should not move a candidate up or down the list.
    """
    ranked = sorted(
        opinions,
        key=lambda o: (o.rank_key if o.grounded else 1, o.symbol),
    )
    return tuple(o.symbol for o in ranked)
