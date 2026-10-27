"""The rules book's 9:45 pick: what fills the 40% sleeve Gemma used to choose for.

Operator 2026-10-26: "stop Gemma from picking, and have the code find the stocks". From the
S&P 500 pool (advisor/gemma_pool.py, the scanner's floor), the names that pass the plan's buy
checks (satellite_feed.buyable_pool, then refusals for the sector cap and recent sales) are
ranked by 12-1 momentum, strongest first, and the top ones fill the free slots. Equal size,
2.5 ATR stop, no target; the plan's exits sell (execution/satellite.py). No model is asked.

The book keeps the `gem-` order ids so the positions held at the change keep their exits.

PURE: rows in, new lists out. scripts/rules_pick.py does the reading and the queueing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from scanner.advisor.gemma_pool import PoolRow


def ranked(rows: Iterable[PoolRow], exclude: Iterable[str] = ()) -> list[PoolRow]:
    """Strongest 12-1 momentum first, unknown momentum last; `exclude` (held, other books) left out."""
    skip = set(exclude)
    keep = [r for r in rows if r.symbol not in skip]
    return sorted(keep, key=lambda r: (r.mom_12_1 is None, -(r.mom_12_1 or 0.0), r.symbol))


def choose(candidates: Sequence[PoolRow], *, refused: Mapping[str, str], slots: int) -> list[PoolRow]:
    """The first `slots` candidates the buy checks did not refuse, in order."""
    if slots <= 0:
        return []
    return [r for r in candidates if r.symbol not in refused][:slots]


def free_slots(*, max_positions: int, held: int, working: int) -> int:
    return max(0, max_positions - held - working)


def queue_item(row: PoolRow, *, rank: int, passing: int) -> dict[str, Any]:
    """A buy_queue item: the plan's only menu choices, and the reason the audit log keeps."""
    mom = f"{row.mom_12_1:+.0f}%" if row.mom_12_1 is not None else "n/a"
    return {"reason": f"rules: #{rank} of {passing} passing the buy checks by 12-1 momentum ({mom})",
            "stop": "normal", "target": "none", "size": "normal"}
