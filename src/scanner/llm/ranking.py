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
                    "veto": {"type": "boolean"},
                    "veto_reason": {"type": "string"},
                },
                "required": ["symbol", "conviction", "concern", "driver",
                             "evidence_quote", "veto", "veto_reason"],
            },
        }
    },
    "required": ["opinions"],
}

_PROMPT = """A mechanical screen has already found a valid structural setup in each company below. It checked the pattern, the stop, the reward:risk and the liquidity. You are not re-checking any of that, and you cannot see the chart.

WHAT THE SCREEN DOES NOT KNOW. It has no opinion about trend: it flags a setup in a company that has fallen all year exactly as readily as one in a market leader. And it cannot read. So you are given two things it could not use — the company's recent news, and a few facts it computed and discarded.

Your question for each company: taking the news and those facts TOGETHER, is there a reason this particular setup is less trustworthy than the others here?

A falling stock whose news explains the fall is a different case from a falling stock with no news at all. The first is a company in trouble. The second may be exactly the shakeout this strategy trades. Judge the combination, not either half alone.

conviction:
- strong   nothing works against it; the decline or the move is unexplained
- neutral  ordinary news; nothing that changes what the price means
- weak     something makes the price structure unreliable — a merger or acquisition, litigation, a restructuring, a guidance withdrawal, a sustained decline the news accounts for, or a move entirely explained by one announcement

YOU MAY REFUSE. Set veto true to stop the trade outright, with veto_reason giving the ground in a few words. Refuse when you can name what is wrong: a pending merger, active litigation, a withdrawn outlook, or a decline whose cause is in the headlines and has not finished. Do NOT refuse because a stock is merely weak — weakness into a sweep is the pattern being traded. Refuse when you can say what is wrong, never when you are simply unsure. A veto with no quote behind it is discarded, so do not guess.

driver: what is moving this stock, judged from the headlines —
  "company"  something specific to this business
  "sector"   something affecting its whole sector (an oil price move, a rate
             decision for banks, a drug-pricing rule for health care)
  "market"   a broad risk-on or risk-off move affecting everything
  "none"     no news explains it
A move caused by a sector-wide event is not the same event as one caused by the company, even when the chart looks identical.

concern: name the specific worry in a few words, or "none".

evidence_quote: copy a span VERBATIM from THAT COMPANY'S OWN headlines. Copy it exactly. Every company below has its own list — a quote taken from another company's headlines is discarded and its veto with it. If a company has no news, use an empty string and say conviction "neutral".

Do not repeat the figures back, and invent no prices, targets or percentages of your own. The numbers are context for your judgement, not output.

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
    #: A refusal to trade this candidate at all, with `veto_reason` saying
    #: why. Honoured only when `grounded` is true and
    #: `policy.llm_veto_enabled` is set, so an ungrounded opinion can
    #: colour a ranking but never block an order.
    veto: bool = False
    veto_reason: str = ""

    @property
    def rank_key(self) -> int:
        """Lower sorts first."""
        return {"strong": 0, "neutral": 1, "weak": 2}.get(self.conviction, 1)


def _render(
    candidates: dict[str, tuple[NewsItem, ...]],
    sectors: dict[str, str] | None = None,
    sector_rs: dict[str, float] | None = None,
    context: dict[str, dict] | None = None,
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
        # PRICE CONTEXT, ADDED 2026-09-28.
        #
        # The model used to see a ticker, a sector and five headlines. It
        # could not have known MLM was 14% below its 200-day average and
        # lagging the index by 29 points, because nothing told it. Both
        # positions opened that day were deep laggards and the screen has
        # no opinion about trend.
        #
        # These are facts the scan already computed and threw away. Giving
        # the model arithmetic it cannot do itself is the same reasoning
        # that puts sector standing here rather than asking for it.
        facts = (context or {}).get(symbol) or {}
        lines = []
        if facts.get('rs') is not None:
            way = 'leading' if facts['rs'] > 0 else 'lagging'
            lines.append(f"    12-1 vs S&P 500: {facts['rs']:+.1f}pp ({way})")
        if facts.get('from_200') is not None:
            side = 'above' if facts['from_200'] >= 0 else 'below'
            lines.append(f"    200-day average: {abs(facts['from_200']):.1f}% {side}")
        if facts.get('from_52w_high') is not None:
            lines.append(f"    from 52-week high: {facts['from_52w_high']:+.1f}%")
        if facts.get('reward_risk') is not None:
            lines.append(f"    setup reward:risk {facts['reward_risk']:.2f},"
                         f" stop {facts.get('risk_pct', 0):.2f}% away")
        joined = ('\n').join(lines)
        body = (joined + '\n' if lines else '') + headlines
        blocks.append('[' + symbol + ']' + note + '\n' + body)
    return "\n\n".join(blocks)


async def rank(
    candidates: dict[str, tuple[NewsItem, ...]],
    client: httpx.AsyncClient,
    sectors: dict[str, str] | None = None,
    sector_rs: dict[str, float] | None = None,
    context: dict[str, dict] | None = None,
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
            _PROMPT.format(companies=_render(candidates, sectors, sector_rs,
                                             context)),
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
            # An UNGROUNDED opinion may not block a trade. The quote check is
            # the only defence against a confident sentence with nothing behind
            # it, and a veto is the most consequential thing the model can say.
            veto=bool(entry.get("veto")) and grounded,
            veto_reason=str(entry.get("veto_reason", ""))[:120],
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
