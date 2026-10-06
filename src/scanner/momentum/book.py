"""Three tranches, each a third of the money, one rebuilt every month.

WHY TRANCHES
A single book rebuilt every three months bets everything on three dates a
year. Three books, each held three months and rebuilt in turn, is the
overlapping-portfolio construction the backtest measured (63-day hold, a new
book every 21 days). It also cuts turnover per rebalance to about a third.

The broker cannot tell tranches apart -- it only sees total shares -- so the
split lives here and in the saved state; `target` adds the tranches up and
`orders` turns the difference from what is held into trades.

Every function returns a new object. Nothing here mutates its input.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from scanner.models import Frozen

TRANCHES = 3


class Tranche(Frozen):
    index: int
    formed: date | None
    holdings: dict[str, int]
    cash: float


class Book(Frozen):
    tranches: tuple[Tranche, ...]


def new_book(capital: float) -> Book:
    """All cash, split evenly. Tranches fill one per month from here."""
    share = capital / TRANCHES
    return Book(tranches=tuple(Tranche(index=i, formed=None, holdings={}, cash=share)
                               for i in range(TRANCHES)))


def tranche_for(day: date) -> int:
    """The tranche whose turn it is in `day`'s calendar month."""
    return (day.year * 12 + day.month - 1) % TRANCHES


def due(book: Book, day: date) -> int | None:
    """The tranche to rebuild on `day`, or None if this month's is done."""
    idx = tranche_for(day)
    formed = book.tranches[idx].formed
    if formed is not None and (formed.year, formed.month) == (day.year, day.month):
        return None
    return idx


def value(tranche: Tranche, prices: Mapping[str, float]) -> float:
    """Cash plus holdings at `prices`. A missing price raises rather than
    guessing -- a held name with no quote needs a human, not a zero."""
    return tranche.cash + sum(shares * prices[symbol] for symbol, shares in tranche.holdings.items())


def rebuild(tranche: Tranche, day: date, *, picks: tuple[str, ...],
            prices: Mapping[str, float], invested: bool) -> Tranche:
    """Sell the tranche at `prices`, then buy `picks` equal weight in whole
    shares, or hold it all in cash when the trend filter is off."""
    total = value(tranche, prices)
    if not invested or not picks:
        return Tranche(index=tranche.index, formed=day, holdings={}, cash=total)
    per_name = total / len(picks)
    holdings = {symbol: int(per_name // prices[symbol]) for symbol in picks}
    holdings = {symbol: shares for symbol, shares in holdings.items() if shares > 0}
    spent = sum(shares * prices[symbol] for symbol, shares in holdings.items())
    return Tranche(index=tranche.index, formed=day, holdings=holdings, cash=total - spent)


def replace_tranche(book: Book, tranche: Tranche) -> Book:
    return Book(tranches=tuple(tranche if t.index == tranche.index else t for t in book.tranches))


def target(book: Book) -> dict[str, int]:
    """Total shares per symbol across all tranches."""
    total: dict[str, int] = {}
    for tranche in book.tranches:
        for symbol, shares in tranche.holdings.items():
            total[symbol] = total.get(symbol, 0) + shares
    return total


def orders(current: Mapping[str, int], wanted: Mapping[str, int]) -> tuple[tuple[str, str, int], ...]:
    """(symbol, side, qty) to move `current` to `wanted`: sells first, so a
    cash account frees money before it spends it; then buys. Alphabetical
    within each, so the list is repeatable."""
    sells, buys = [], []
    for symbol in sorted(set(current) | set(wanted)):
        diff = wanted.get(symbol, 0) - current.get(symbol, 0)
        if diff < 0:
            sells.append((symbol, "sell", -diff))
        elif diff > 0:
            buys.append((symbol, "buy", diff))
    return tuple(sells + buys)
