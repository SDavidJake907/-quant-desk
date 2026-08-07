"""Does this name still trade on its own chart?

WHAT THIS IS FOR, AND WHAT IT IS NOT
The setup is chosen by arithmetic over bars, and that does not change. This
adds one question before an order goes out: has something happened to the
company that makes its price structure meaningless?

The case that produced this module: on 2026-08-06 the scanner found a textbook
sweep-CHoCH-BOS on SUPN and sized an order. Supernus had announced an
ALL-STOCK MERGER with Indivior three days earlier. A stock in a live deal
tracks the exchange ratio, not its own support and resistance — the pattern
was real and the mechanism behind it was gone.

THE MODEL STILL EMITS NO NUMBER
Gemma labels a headline's category and whether it is material. It does not set
an entry, a stop, a target or a size, and it cannot veto a trade on its own —
`blocking` is decided here, from categories, in ordinary Python.
"""

from __future__ import annotations

import httpx

from scanner.execution.guards import Verdict
from scanner.models import Frozen

#: Categories that change what the price MEANS rather than merely moving it.
#:
#: Note "mna" is deliberately NOT here. Being acquired and acquiring are
#: opposite situations and the category cannot tell them apart — it blocked
#: AMD for buying a startup, which is ordinary business news. Deal exposure is
#: decided by `deal_role` instead: only a TARGET is structurally repriced.
STRUCTURAL = frozenset({"legal"})

#: SEC item codes that say the same thing without a model involved.
STRUCTURAL_ITEMS = ("1.01", "1.02", "2.01", "8.01")


class NewsFlag(Frozen):
    """What the news and calendar layers found for one symbol."""

    symbol: str
    categories: tuple[str, ...] = ()
    headlines: tuple[str, ...] = ()
    filings: tuple[str, ...] = ()
    checked: bool = True
    #: "target" when this company is the one being bought. Its price then
    #: tracks an exchange ratio rather than its own business, so structure
    #: means nothing until the deal resolves — and there is nothing to wait
    #: for, which is why a target is refused outright rather than watch-listed.
    deal_role: str = "none"
    #: Set when the company reports inside the holding period. Earnings are
    #: the most predictable gap event there is, and gapped stops are this
    #: strategy's fat left tail — the five worst trades in the study were all
    #: gaps, worst -3.56R against a -1.00R mean for normal stops.
    earnings: str = ""

    @property
    def structural(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.categories) & STRUCTURAL))

    @property
    def is_takeover_target(self) -> bool:
        return self.deal_role == "target"

    @property
    def blocking(self) -> bool:
        return bool(self.structural) or bool(self.earnings) or self.is_takeover_target

    def verdict(self) -> Verdict:
        if not self.checked:
            return Verdict(allowed=True, reason="news not checked")
        if self.earnings:
            return Verdict(allowed=False, reason=f"reports earnings {self.earnings}")
        if self.is_takeover_target:
            return Verdict(allowed=False, reason="being acquired — price tracks the deal")
        if self.structural:
            return Verdict(
                allowed=False,
                reason=f"structural news ({', '.join(self.structural)})",
            )
        return Verdict(allowed=True)


async def check(
    symbol: str,
    client: httpx.AsyncClient,
    names=None,
    earnings: dict | None = None,
) -> NewsFlag:
    """Triage the symbol's recent news. Never raises — a news outage must not
    stop trading, it just means the question went unanswered."""
    from scanner.pipeline import scan_ticker

    due = (earnings or {}).get(symbol)
    scheduled = due.label if due is not None else ""

    try:
        report = await scan_ticker(symbol, client, names=names)
    except Exception:
        return NewsFlag(symbol=symbol, checked=False, earnings=scheduled)
    if report is None:
        return NewsFlag(symbol=symbol, checked=False, earnings=scheduled)

    roles = {t.verdict.deal_role for t in report.items}
    return NewsFlag(
        symbol=symbol,
        earnings=scheduled,
        deal_role="target" if "target" in roles else "none",
        categories=tuple(t.verdict.category for t in report.items),
        headlines=tuple(t.item.title for t in report.items)[:4],
        filings=tuple(
            f.summary for f in report.filings
            if any(code in f.summary for code in STRUCTURAL_ITEMS)
        )[:3],
    )
