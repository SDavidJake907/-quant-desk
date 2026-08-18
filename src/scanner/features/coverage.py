"""Trading days that quietly went missing from a bar series.

WHAT THIS EXISTS FOR
On 2026-08-18, every one of 120 S&P 500 symbols had a NULL close for Monday
2026-08-17 in Yahoo's daily series. The bar was there; its close was None. The
parser skips those rather than inventing a price — correct — and it does so in
silence, which is not.

A whole trading day disappeared from every daily series at once and nothing
said a word. Intraday bars were unaffected, so the live scanner ran normally
and looked healthy. Only daily-bar research was wrong, and only invisibly.

That is the same shape as the adjusted-price bug: not wrong arithmetic, just
data quietly absent while every number still prints. A ten-year backtest with
scattered missing days reports its result with no hint that anything is gone.

HOW THE TRUTH IS ESTABLISHED WITHOUT A CALENDAR
There is no exchange calendar here without a broker call, so coverage is
measured against the other symbols. A day that most symbols have and one lacks
is that symbol's problem; a day almost nobody has is the provider's. The
distinction matters because the responses differ — refetch one name, or do not
trust the day at all.

WHAT IS DELIBERATELY NOT A GAP
The stretch before a symbol's first bar or after its last. A recent listing has
no history before it existed and a delisting has none after; reporting those as
missing would bury the real gaps in thousands of lines of noise.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Iterable, Mapping

from scanner.models import Frozen

#: At or below this share of symbols present, the day is the provider's fault
#: rather than any one symbol's. Ten percent is well clear of the scattered
#: single-name gaps seen in practice (6% of symbols on one day) and far below
#: a genuine market-wide outage (100%).
PROVIDER_WIDE_PCT = 10.0


class Gap(Frozen):
    """One trading day some symbols have and others do not."""

    day: date
    present: int
    total: int
    #: Symbols missing the day, only those whose series spans it.
    missing: tuple[str, ...]

    @property
    def coverage_pct(self) -> float:
        return self.present / self.total * 100.0 if self.total else 0.0

    @property
    def is_provider_wide(self) -> bool:
        """Whether this looks like the data source rather than one symbol."""
        return self.coverage_pct <= PROVIDER_WIDE_PCT

    def describe(self) -> str:
        if self.is_provider_wide:
            return (f"{self.day}  PROVIDER-WIDE — only {self.present} of "
                    f"{self.total} symbols have this day")
        shown = ", ".join(self.missing[:8])
        more = f" (+{len(self.missing) - 8} more)" if len(self.missing) > 8 else ""
        return (f"{self.day}  missing from {len(self.missing)} symbol(s): "
                f"{shown}{more}")


def find_gaps(series: Mapping[str, Iterable[date]]) -> tuple[Gap, ...]:
    """Days present for some symbols and absent for others, newest first.

    A symbol is only counted as missing a day when its own series spans that
    day — between its first and last bar. Otherwise every recent listing would
    report years of absence it could not possibly have.
    """
    spans: dict[str, tuple[date, date]] = {}
    dates: dict[str, set[date]] = {}
    for symbol, days in series.items():
        owned = {d for d in days}
        if not owned:
            continue                      # nothing to say about an empty series
        dates[symbol] = owned
        spans[symbol] = (min(owned), max(owned))

    if not dates:
        return ()

    seen: Counter[date] = Counter()
    for owned in dates.values():
        seen.update(owned)

    gaps: list[Gap] = []
    for day, present in seen.items():
        # Only symbols whose history covers this day can be missing it.
        eligible = [s for s, (first, last) in spans.items() if first <= day <= last]
        missing = tuple(sorted(s for s in eligible if day not in dates[s]))
        if not missing:
            continue
        gaps.append(Gap(day=day, present=present, total=len(eligible),
                        missing=missing))
    return tuple(sorted(gaps, key=lambda g: g.day, reverse=True))
