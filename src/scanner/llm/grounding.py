"""What counts as evidence for a model verdict.

The repo rule is "no citation, no claim": every verdict must quote a span that
verbatim occurs in the source text. Verbatim-occurrence alone turns out to be
a weak test, because the cheapest way to satisfy it is to copy the headline —
and copying the headline is what the model actually does when it has no
particular reason for its answer.

Observed on 2026-08-05, live: a market roundup was accepted as `subject` with
the evidence "Nvidia Lifts Dow", and a four-company listicle with "AI
Trailblazers Nvidia". Both quotes are perfectly verbatim. Neither is a reason.

So grounding is split into two questions:

  1. Does the quote exist in the source?      (catches fabrication)
  2. Is the quote actually a citation?        (catches shrugging)

A span that only names the company, or that swallows the entire article, is
not evidence of anything. Rejecting those costs recall — that is the point,
and the funnel reports what it dropped rather than hiding it.
"""

from __future__ import annotations

import re

from scanner.models import NewsItem

#: A quote shorter than this is a label, not a citation.
MIN_QUOTE_WORDS = 4
MIN_QUOTE_CHARS = 20

#: Above this share of the source text, the "quote" is just a copy of it.
MAX_QUOTE_SHARE = 0.9

#: Only applied to sources long enough to contain a distinguishable span;
#: for a short headline, the whole headline genuinely is the evidence.
WHOLESALE_MIN_SOURCE_CHARS = 80

_WORD = re.compile(r"[\w'&%$.-]+")


class Rejection:
    """Why a quote was refused. Values double as report labels."""

    EMPTY = "empty"
    NOT_VERBATIM = "not_verbatim"
    TOO_SHORT = "too_short"
    WHOLESALE_COPY = "wholesale_copy"


def source_text(item: NewsItem) -> str:
    return f"{item.title} {item.summary}".strip()


def occurs_verbatim(quote: str, item: NewsItem) -> bool:
    """The original guard: the span must genuinely appear in the source."""
    return quote.strip().lower() in source_text(item).lower()


def is_substantive(quote: str) -> bool:
    """A citation has to say something beyond naming the company.

    "AI Trailblazers Nvidia" identifies a subject; it does not justify calling
    the article one.
    """
    stripped = quote.strip()
    return (
        len(stripped) >= MIN_QUOTE_CHARS
        and len(_WORD.findall(stripped)) >= MIN_QUOTE_WORDS
    )


def is_wholesale_copy(quote: str, item: NewsItem) -> bool:
    """True when the model quoted essentially the whole source.

    Copying everything always satisfies verbatim-occurrence, so it carries no
    information about why the verdict was reached. Short sources are exempt:
    there, the whole text really is the only available span.
    """
    source = source_text(item)
    if len(source) < WHOLESALE_MIN_SOURCE_CHARS:
        return False
    return len(quote.strip()) >= MAX_QUOTE_SHARE * len(source)


def reject_reason(quote: str, item: NewsItem) -> str | None:
    """None if the quote is acceptable evidence, else why it is not."""
    if not quote.strip():
        return Rejection.EMPTY
    if not occurs_verbatim(quote, item):
        return Rejection.NOT_VERBATIM
    if not is_substantive(quote):
        return Rejection.TOO_SHORT
    if is_wholesale_copy(quote, item):
        return Rejection.WHOLESALE_COPY
    return None


def is_grounded(quote: str, item: NewsItem) -> bool:
    return reject_reason(quote, item) is None
