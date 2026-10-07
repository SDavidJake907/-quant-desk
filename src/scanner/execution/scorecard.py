"""Results split by strategy (WORKLIST §37).

The question the operator asked: do my picks beat the scanner, and does
momentum beat both? Every closed trade's `trade_outcome` carries its
client_order_id, and the prefix names the strategy, so the split needs no new
record -- only grouping.

Momentum does not close trades one by one (tranches turn over monthly), so it
is scored as a book: value now against the capital it started with.

PURE. Reads parsed audit rows; prints nothing.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from scanner.execution.ownership import strategy_of
from scanner.models import Frozen


class Stats(Frozen):
    strategy: str
    trades: int
    wins: int
    total_r: float
    total_dollars: float

    @property
    def mean_r(self) -> float:
        return self.total_r / self.trades if self.trades else 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0


def read_rows(path: str | Path) -> list[dict[str, Any]]:
    """Audit rows, skipping a torn line rather than losing the log."""
    log = Path(path)
    if not log.exists():
        return []
    rows = []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def by_strategy(rows: Iterable[Mapping[str, Any]]) -> dict[str, Stats]:
    """Closed-trade stats per strategy, one count per client_order_id."""
    seen: set[str] = set()
    acc: dict[str, list[float]] = {}
    for row in rows:
        if row.get("event") != "trade_outcome":
            continue
        out = row.get("outcome") or {}
        coid = str(out.get("client_order_id") or "")
        if not coid or coid in seen:
            continue
        seen.add(coid)
        r = float(out.get("realized_r") or 0.0)
        dollars = float(out.get("realized_dollars") or 0.0)
        name = strategy_of(coid)
        n, wins, total_r, total_d = acc.get(name, [0, 0, 0.0, 0.0])
        acc[name] = [n + 1, wins + (r > 0), total_r + r, total_d + dollars]
    return {name: Stats(strategy=name, trades=int(v[0]), wins=int(v[1]),
                        total_r=v[2], total_dollars=v[3])
            for name, v in sorted(acc.items())}


def book_return(start_value: float, cash: float,
                market_value: float) -> float | None:
    """Momentum book return since its first paper rebalance, in percent."""
    if start_value <= 0:
        return None
    return ((cash + market_value) / start_value - 1.0) * 100.0
