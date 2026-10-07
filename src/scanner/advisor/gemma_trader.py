"""Gemma's own book: the junior trader (WORKLIST §42).

THE CHARTER
Gemma runs a real paper book of `gemma_allocation_pct` of the account. Each
morning it chooses up to `gemma_max_positions` names from watchlists/core.txt
(minus anything another book holds) and gives a reason quoting its evidence.
Its goal: beat the S&P 500 and the momentum rule over six months, drawdown
under 25%. If it does, it earns a wider list and a bigger sleeve.

WHAT IT CONTROLS, AND WHAT IT CANNOT
It names stocks. That is all. The answer schema has no numeric field and the
symbol field is an enum of the allowed names, so it cannot emit a price, a
size, a stop, or a ticker outside its list. The system prices, sizes, stops
and sends (execution/bookorders.py), exactly as for the operator's picks.

EVIDENCE OR NO NEW BUY
A NEW name is bought only if its `evidence` occurs verbatim (whitespace and
case aside) in that name's own facts block and is long enough to say
something. A name already held is kept even if its quote fails: an
ungrounded answer may not CHANGE the book, and selling is a change.
A missing, malformed or unreachable answer leaves the book exactly as it is.

PURE. The script fetches facts, calls the model, and trades.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from scanner.models import Frozen

MIN_EVIDENCE_CHARS = 15

CHARTER = (
    "You are the desk's junior trader, running your own real paper book. "
    "Your goal is to earn a real seat: beat the S&P 500 and the desk's momentum "
    "rule over six months, with a drawdown under 25%, and explain every call. "
    "If you succeed you get a wider list and more capital. The system handles "
    "prices, sizes and stops; you only choose which stocks to hold."
)

RULES = (
    "Rules:\n"
    "- Choose at most {n} stocks to HOLD from the list below. Fewer is fine; none is fine.\n"
    "- A stock you hold now and leave out will be SOLD. A new stock you include will be BOUGHT.\n"
    "- For each, give a short reason and an 'evidence' field that copies an exact phrase "
    "from THAT stock's own block below. A new buy without an exact phrase is ignored.\n"
    "- Do not write prices, sizes or percentages of your own. Use only what is below.\n"
    "- What has held up on this desk: buying strength (12-1 momentum) in an S&P uptrend. "
    "RSI, moving averages and chart patterns were tested here and showed no edge.\n"
)


class Decision(Frozen):
    keep: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...] = ()
    reasons: tuple[tuple[str, str], ...] = ()
    journal: str = ""
    unchanged: bool = False


def schema(allowed: Iterable[str], max_positions: int) -> dict[str, Any]:
    """Grammar-constrained answer: symbols from the list, text only."""
    return {
        "type": "object",
        "properties": {
            "holdings": {
                "type": "array", "maxItems": max_positions,
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "enum": list(allowed)},
                        "reason": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["symbol", "reason", "evidence"],
                },
            },
            "journal": {"type": "string"},
        },
        "required": ["holdings", "journal"],
    }


def prompt(facts: Mapping[str, str], *, held: Mapping[str, str], max_positions: int) -> str:
    book = "\n".join(f"- {s}: {status}" for s, status in held.items()) or "- (no positions)"
    blocks = "\n\n".join(f"[{s}]\n{text}" for s, text in facts.items())
    return (f"{CHARTER}\n\n{RULES.format(n=max_positions)}\n"
            f"Your book now:\n{book}\n\nThe list:\n\n{blocks}\n\n"
            "Answer with your holdings and a two-sentence 'journal' entry on today's thinking.")


def _norm(text: str) -> str:
    return " ".join(str(text).split()).lower()


def grounded(evidence: str, block: str) -> bool:
    quote = _norm(evidence)
    return len(quote) >= MIN_EVIDENCE_CHARS and quote in _norm(block)


def validate(answer: Any, facts: Mapping[str, str], *, held: set[str],
             max_positions: int) -> Decision:
    """The holdings to keep after every rule above. Never raises."""
    items = answer.get("holdings") if isinstance(answer, dict) else None
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        return Decision(keep=tuple(sorted(held)), unchanged=True,
                        journal="no usable answer -- book left unchanged")
    keep: list[str] = []
    rejected: list[tuple[str, str]] = []
    reasons: list[tuple[str, str]] = []
    for item in items:
        symbol = str(item.get("symbol", "")).upper()
        if symbol in keep:
            continue
        if symbol not in facts:
            rejected.append((symbol, "not on the list"))
            continue
        if len(keep) >= max_positions:
            rejected.append((symbol, "over the limit"))
            continue
        if symbol not in held and not grounded(item.get("evidence", ""), facts[symbol]):
            rejected.append((symbol, "evidence not found in its facts"))
            continue
        keep.append(symbol)
        reasons.append((symbol, str(item.get("reason", ""))[:300]))
    return Decision(keep=tuple(keep), rejected=tuple(rejected), reasons=tuple(reasons),
                    journal=str(answer.get("journal", ""))[:600])


def diff(keep: Iterable[str], held: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(buys, sells) to move the book from `held` to `keep`."""
    keep, held = tuple(keep), set(held)
    return (tuple(s for s in keep if s not in held),
            tuple(sorted(s for s in held if s not in set(keep))))
