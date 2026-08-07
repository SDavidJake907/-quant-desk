"""The S&P Composite 1500 as one screening universe.

The 500, 400 and 600 are mutually exclusive by construction, so their union is
1,504 distinct companies covering large, mid and small caps — every one of
them carrying a GICS sector, which is what lets the dashboard group anything
by sector without a second data source.

Membership TODAY is accurate for all three lists. Membership in the PAST is
not: `IndexSpec.point_in_time` marks which change logs survive the drift audit
in `index_history`, and `backtest_universe` is the one to use when history
matters. Keeping those two ideas in separate functions is deliberate — the
whole survivorship lesson was that "which companies are in the index" silently
means two different things.
"""

from __future__ import annotations

import asyncio

import httpx

from scanner.models import Frozen
from scanner.sources.index_history import (
    COMPOSITE_1500,
    IndexHistory,
    IndexHistoryError,
    IndexSpec,
    load,
)


class UniverseMember(Frozen):
    """One company, tagged with the index it belongs to."""

    symbol: str
    name: str
    sector: str
    index: str


def merge(histories: tuple[IndexHistory, ...]) -> tuple[UniverseMember, ...]:
    """Flatten several indices into one universe, first listing wins.

    The indices are disjoint in principle; in practice a name caught mid-move
    between them can appear on two pages for a few days. First-wins keeps it
    counted once rather than double-weighting it.
    """
    seen: set[str] = set()
    members: list[UniverseMember] = []
    for history in histories:
        for member in history.members:
            if member.symbol in seen:
                continue
            seen.add(member.symbol)
            members.append(
                UniverseMember(
                    symbol=member.symbol,
                    name=member.name,
                    sector=member.sector or "Unclassified",
                    index=history.spec.label,
                )
            )
    return tuple(members)


async def load_indices(
    client: httpx.AsyncClient, specs: tuple[IndexSpec, ...] = COMPOSITE_1500
) -> tuple[IndexHistory, ...]:
    """Load several index pages concurrently, skipping any that fail.

    One unreachable Wikipedia page should shrink the universe, not empty it.
    """
    settled = await asyncio.gather(
        *(load(client, spec=spec) for spec in specs), return_exceptions=True
    )
    histories: list[IndexHistory] = []
    for spec, outcome in zip(specs, settled):
        if isinstance(outcome, IndexHistory):
            histories.append(outcome)
        else:
            print(f"  ! {spec.label} unavailable: {outcome}")
    if not histories:
        raise IndexHistoryError("no index membership could be loaded")
    return tuple(histories)


async def load_universe(
    client: httpx.AsyncClient, specs: tuple[IndexSpec, ...] = COMPOSITE_1500
) -> tuple[UniverseMember, ...]:
    """Today's screening universe. Accurate for every index in `specs`."""
    return merge(await load_indices(client, specs))


def backtest_specs(specs: tuple[IndexSpec, ...] = COMPOSITE_1500) -> tuple[IndexSpec, ...]:
    """The subset whose history is good enough to backtest on.

    Excluding an index here costs breadth. Including one whose log has holes
    costs correctness silently, which is worse.
    """
    return tuple(spec for spec in specs if spec.point_in_time)
