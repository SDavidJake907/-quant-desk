"""Where setups go when a scan reports none.

"0 fresh setups" is three different sentences wearing one coat:

    the screen found nothing at all           -> a quiet market
    it found setups and they aged out         -> the freshness window is wrong
    it found setups already past their level  -> the scan cadence is too slow

Those have different fixes and the log could not tell them apart. On
2026-08-17 every in-hours run printed zero while the after-close run found
two, and reconstructing why afterwards was impossible because the intraday
bars had already moved on. Counting costs nothing and makes the question
answerable the next morning instead of never.

This records. It decides nothing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Funnel:
    """Tally of one scan, stage by stage."""

    symbols: int = 0
    setups: int = 0
    dropped_stale: int = 0
    dropped_not_live: int = 0
    dropped_slot: int = 0
    candidates: int = 0
    #: Age in bars of the freshest setup that was still refused as stale. If
    #: this sits far above `fresh_bars` on a day with no trades, the window is
    #: the constraint; if it hovers just above, the scan cadence is.
    oldest_rejected_age: int = 0
    fresh_bars: int = 0

    def scanned(self, symbols: int) -> None:
        self.symbols = symbols

    def setup_found(self) -> None:
        self.setups += 1

    def dropped_as_stale(self, *, age: int, fresh_bars: int) -> None:
        self.dropped_stale += 1
        self.fresh_bars = fresh_bars
        self.oldest_rejected_age = max(self.oldest_rejected_age, age)

    def passed_freshness(self) -> None:
        """No counter of its own: what survives is implied by what did not."""

    def dropped_as_not_live(self) -> None:
        self.dropped_not_live += 1

    def passed_live(self) -> None:
        """See `passed_freshness`."""

    def dropped_by_slot(self) -> None:
        self.dropped_slot += 1

    def became_candidate(self) -> None:
        self.candidates += 1

    def summary(self) -> str:
        """One line, for the scheduled log."""
        parts = [
            f"funnel: {self.symbols} symbols",
            f"setups {self.setups}",
            f"stale {self.dropped_stale}",
            f"not live {self.dropped_not_live}",
            f"slot/weekday {self.dropped_slot}",
            f"candidates {self.candidates}",
        ]
        if self.dropped_stale:
            parts.append(
                f"oldest rejected {self.oldest_rejected_age} bars "
                f"(window {self.fresh_bars})"
            )
        return "  ".join(parts)
