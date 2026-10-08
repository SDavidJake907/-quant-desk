"""Which stocks does each headline touch? (WORKLIST §52)

Gemma reads the morning's headlines and the President's posts and links each
market-relevant one to the stocks and sectors it touches. Same rails as the
trader prompt (advisor/gemma_trader.py):
  - headline ids, symbols, sectors and the effect are ENUMS: it cannot name a
    stock outside the desk's lists or invent a headline;
  - every link must quote the headline verbatim, or it is dropped;
  - a missing or malformed answer yields no links, never an exception.

"effect" is its READ of the headline (helps / hurts / mixed / unclear), labelled
as such wherever it is shown. It is not a price forecast: news is usually in the
price within minutes (WORKLIST §49), which is why the brief says so.

PURE.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from scanner.models import Frozen

EFFECTS = ("helps", "hurts", "mixed", "unclear")
MIN_QUOTE_CHARS = 8
MAX_LINKS = 12


class Impact(Frozen):
    headline: str
    symbols: tuple[str, ...]
    sectors: tuple[str, ...]
    effect: str
    why: str
    quote: str


def schema(headlines: Mapping[str, str], symbols: Iterable[str],
           sectors: Iterable[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "links": {
                "type": "array", "maxItems": MAX_LINKS,
                "items": {
                    "type": "object",
                    "properties": {
                        "headline": {"type": "string", "enum": list(headlines)},
                        "symbols": {"type": "array",
                                    "items": {"type": "string", "enum": list(symbols)}},
                        "sectors": {"type": "array",
                                    "items": {"type": "string", "enum": list(sectors)}},
                        "effect": {"type": "string", "enum": list(EFFECTS)},
                        "why": {"type": "string"},
                        "quote": {"type": "string"},
                    },
                    "required": ["headline", "symbols", "sectors", "effect", "why", "quote"],
                },
            },
        },
        "required": ["links"],
    }


def prompt(headlines: Mapping[str, str], universe: Mapping[str, str]) -> str:
    """`universe`: symbol -> sector for every stock either book holds or may pick."""
    names = "\n".join(f"- {s} ({sec})" for s, sec in universe.items())
    news = "\n".join(f"{hid}: {text}" for hid, text in headlines.items())
    return "\n".join((
        "You link this morning's news to the stocks it touches, for a stock desk.",
        "",
        "Rules:",
        "- Pick only headlines that can move markets: rates and the Fed, inflation, oil and "
        "energy, wars and sanctions, tariffs and trade, taxes, regulation, AI spending, or "
        "news about a named company or sector. Skip politics, celebrity and ceremony.",
        "- For each, list the stocks below it touches (directly or through their sector) and "
        "the sectors. Use only the stocks listed. A headline that touches the whole market "
        "gets sectors and no stocks.",
        "- 'effect' is your read for those stocks: helps, hurts, mixed or unclear. Say "
        "unclear when it is.",
        "- 'quote' must copy words exactly from that headline.",
        "- Do not predict prices or invent facts. Fewer, surer links beat many weak ones.",
        "",
        "The desk's stocks:", names, "",
        "The news:", news,
    ))


def _norm(text: str) -> str:
    return " ".join(str(text).split()).lower()


def validate(answer: Any, headlines: Mapping[str, str], symbols: Iterable[str],
             sectors: Iterable[str]) -> tuple[Impact, ...]:
    """Links that pass every rule above. Never raises."""
    links = answer.get("links") if isinstance(answer, dict) else None
    if not isinstance(links, list):
        return ()
    known, sects = set(symbols), set(sectors)
    out = []
    for link in links:
        if not isinstance(link, dict):
            continue
        text = headlines.get(str(link.get("headline", "")))
        quote = _norm(link.get("quote", ""))
        if not text or len(quote) < MIN_QUOTE_CHARS or quote not in _norm(text):
            continue
        syms = tuple(dict.fromkeys(s for s in link.get("symbols") or () if s in known))
        secs = tuple(dict.fromkeys(s for s in link.get("sectors") or () if s in sects))
        effect = link.get("effect") if link.get("effect") in EFFECTS else "unclear"
        if syms or secs:
            out.append(Impact(headline=text, symbols=syms, sectors=secs, effect=effect,
                              why=str(link.get("why", ""))[:200], quote=str(link.get("quote", ""))))
    return tuple(out)
