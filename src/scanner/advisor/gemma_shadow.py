"""Shadow Gemma: variants asked live Gemma's exact question, graded, never traded.

RULES, SET 2026-10-19 BEFORE ANY SHADOW ANSWERED
- Same question: the prompt, schema, facts and holdings live Gemma was given
  that morning (saved by gemma_trade.py), asked after her orders went out.
- Variants:
    think  -- she thinks first (thinking on, at most 4,096 tokens: unlimited, it
              looped until the context ran out), then answers from her notes
              with thinking off, temperature 0
    vote3  -- thinking off as live, three answers at temperature 0.7 (seeds
              1-3); a stock is held if 2 of the 3 hold it
- Every answer passes gemma_trader.validate exactly like hers (list, limit,
  verbatim evidence). Whether the model kept to the schema is recorded too.
- Grade: per morning, the variant's chosen-minus-passed-over spread (ops/
  selection.py) minus live Gemma's on the same menu, 5 and 21 sessions on;
  ops/answer.py reads the paired differences with the penalty for 2 variants.
- Promotion: a variant replaces live Gemma's setting only through a FREEZE
  window, after 40+ graded mornings at 5 sessions with verdict REAL.

PURE. scripts/gemma_shadow.py asks the model.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from scanner.advisor.gemma_trader import Decision, Menu
from scanner.models import Frozen

VARIANTS = ("think", "vote3")
VOTES_NEEDED = 2
PROMOTE_AFTER_DAYS = 40


class ShadowRecord(Frozen):
    question: str            # the saved question's id
    day: str
    session: str
    variant: str
    keep: tuple[str, ...]
    live_keep: tuple[str, ...]
    menu: tuple[str, ...]
    journal: str = ""
    rejected: tuple[tuple[str, str], ...] = ()
    schema_kept: bool = True
    seconds: float = 0.0
    answers: int = 1


def vote(decisions: Sequence[Decision], max_positions: int,
         needed: int = VOTES_NEEDED) -> tuple[str, ...]:
    """Stocks held by at least `needed` answers; most votes first, then the
    order the first answer gave them."""
    counts = Counter(s for d in decisions for s in d.keep)
    order: dict[str, int] = {}
    for s in (s for d in decisions for s in d.keep):
        order.setdefault(s, len(order))
    winners = [s for s, n in counts.items() if n >= needed]
    return tuple(sorted(winners, key=lambda s: (-counts[s], order[s]))[:max_positions])


def kept_schema(answer: Any, allowed: Iterable[str], menu: Menu | None) -> bool:
    """Whether a raw answer stayed inside the schema the sampler was given:
    Ollama can silently drop `format` on models with a thinking template."""
    if not isinstance(answer, dict) or not isinstance(answer.get("holdings"), list):
        return False
    allowed = set(allowed)
    for item in answer["holdings"]:
        if not isinstance(item, dict) or item.get("symbol") not in allowed:
            return False
        if menu is not None and not (item.get("stop") in menu.stops
                                     and item.get("target") in menu.targets
                                     and item.get("size") in menu.sizes):
            return False
    return isinstance(answer.get("journal"), str)


def paired(records: Iterable[ShadowRecord], variant: str,
           spread_of: Mapping[tuple[str, tuple[str, ...]], float | None]) -> list[float]:
    """Per graded morning: the variant's spread minus live Gemma's, in points.
    `spread_of[(day, chosen)]` is the selection spread for that choice."""
    out = []
    for r in records:
        if r.variant != variant:
            continue
        mine, live = spread_of.get((r.day, r.keep)), spread_of.get((r.day, r.live_keep))
        if mine is not None and live is not None:
            out.append(mine - live)
    return out
