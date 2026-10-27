"""The momentum engine's monthly rebalance -- DRY RUN ONLY.

    python scripts/momentum_rebalance.py                 # this month's tranche, recorded
    python scripts/momentum_rebalance.py --preview       # show it, change nothing
    python scripts/momentum_rebalance.py --capital 50000 # first run only: book size

WHAT IT DOES (the measured design, WORKLIST §31 arm A):
  1  universe: today's S&P 500 + MidCap 400 members (operator's choice)
  2  rank by 12-1 momentum from daily closes up to the LAST COMPLETED session
  3  trend filter: S&P 500 above its 210-day average, else this tranche -> cash
  4  rebuild the one tranche whose month it is (three tranches, one a month)
  5  print the trades that would move the book there, and record them

PAPER MODE (WORKLIST §37, 2026-10-06):
    python scripts/momentum_rebalance.py --paper            # show paper trades
    python scripts/momentum_rebalance.py --paper --submit   # send them (MOC)
A separate book (paper_book.json) sized at `momentum_allocation_pct` of
equity. Picks held or ordered by another strategy are dropped first, so two
strategies never share one Alpaca position; orders are market-on-close with
`mom-SYMBOL-YYYYMM-side` ids, so a retry can never double one. The dry run
keeps running unchanged as the engine's clean record.

Fills are assumed at the last completed close; the backtest entered at the
next close, so the dry run's prices are a day early. Dry-run state lives in
data/momentum/dry_run_book.json and evolves month to month, so the dry run is
itself a paper record of what the engine would have held.
Logged but NOT enforced, because they were never tested: sector concentration
of the picks, and how much of the book one theme would be.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution import bookorders, ownership
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.audit import append_event
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import EXCHANGE_TZ
from scanner.features.technicals import compute
from scanner.momentum import book as bk
from scanner.momentum import paper, signal, state
from scanner.sources import composite
from scanner.models import AuditEvent
from scanner.sources.index_history import SP400, SP500

STATE = Path("data/momentum/dry_run_book.json")
RECORD = Path("data/momentum/dry_run.jsonl")
BENCHMARK = "^GSPC"
PAPER_STATE = Path("data/momentum/paper_book.json")
PAPER_RECORD = Path("data/momentum/paper.jsonl")
POLICY = TradingPolicy()


class BrokerView:
    """What the paper book needs from the account, read once."""

    def __init__(self, equity, market_open, momentum_held, foreign):
        self.equity = equity
        self.market_open = market_open
        self.momentum_held = momentum_held      # {symbol: shares} owned by momentum
        self.foreign = foreign                  # symbols another strategy holds/orders


async def broker_view() -> BrokerView:
    async with httpx.AsyncClient(timeout=60) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        account = await broker.account()
        positions = await broker._get("/v2/positions")
        open_orders = await broker._get("/v2/orders", status="open", nested="true")
        history = await broker._get("/v2/orders", status="all", limit=500, nested="true")
        market_open = await broker.market_open()
    owner_map = ownership.owners(history)
    held = {p["symbol"]: int(float(p.get("qty") or 0)) for p in positions}
    momentum_held = {s: q for s, q in held.items()
                     if ownership.owner(s, owner_map) == ownership.MOMENTUM}
    foreign = {s for s in held if s not in momentum_held}
    foreign |= {o["symbol"] for o in open_orders
                if ownership.strategy_of(o.get("client_order_id")) != ownership.MOMENTUM}
    return BrokerView(account.equity, market_open, momentum_held, foreign)


async def submit_trades(trades, today) -> list[str]:
    """Send each trade market-on-close; returns the symbols that FAILED. A
    reused client id is an order already sent on an earlier try."""
    failed = []
    async with httpx.AsyncClient(timeout=60) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        for symbol, side, qty in trades:
            payload = paper.moc_payload(symbol, side, qty, today)
            now = datetime.now().astimezone()
            try:
                order = await broker.submit(payload)
                append_event(settings.audit_log_path, AuditEvent(
                    event="momentum_order", at=now, symbol=symbol,
                    order_id=str(order.get("id", "")), request_id=broker.last_request_id,
                    stage="trade", reason=f"{side} {qty} MOC {payload['client_order_id']}",
                    policy=POLICY.fingerprint, build=build_id()))
                print(f"     sent {side:<4} {qty:>6} {symbol:<6} {payload['client_order_id']}")
            except BrokerError as exc:
                if paper.is_duplicate(str(exc)):
                    print(f"     already sent {symbol} ({payload['client_order_id']})")
                    continue
                failed.append(symbol)
                append_event(settings.audit_log_path, AuditEvent(
                    event="momentum_order_failed", at=now, symbol=symbol,
                    request_id=broker.last_request_id, stage="trade",
                    reason=f"{side} {qty}: {exc}",
                    policy=POLICY.fingerprint, build=build_id()))
                print(f"     FAILED {side} {qty} {symbol}: {exc}")
    return failed


def completed(bars, today):
    """Drop a bar dated today: during the session it is still forming."""
    if bars.dates and bars.dates[-1] >= today:
        return bars.upto(len(bars) - 2)
    return bars


async def gather(today):
    async with httpx.AsyncClient(timeout=60) as client:
        members = await composite.load_universe(client, (SP500, SP400))
        sector = {m.symbol: m.sector for m in members}
        symbols = tuple(sorted(sector))
        bench = completed(await fetch_bars(BENCHMARK, client, range_="2y"), today)
        daily, errors = await fetch_many(symbols, client, range_="2y")
    daily = {s: completed(b, today) for s, b in daily.items()}
    return sector, bench, daily, errors


async def prices_for(symbols, daily, today) -> dict[str, float]:
    """Last completed close for every symbol needed, fetching any held name
    that has left the universe since its tranche bought it."""
    out = {s: daily[s].closes[-1] for s in symbols if s in daily and daily[s].closes}
    missing = [s for s in symbols if s not in out]
    if missing:
        async with httpx.AsyncClient(timeout=60) as client:
            extra, _ = await fetch_many(tuple(missing), client, range_="1mo")
        out |= {s: completed(b, today).closes[-1] for s, b in extra.items() if b.closes}
    return out


async def wind_down(view: BrokerView, submit: bool) -> int:
    """Momentum is a helper now (policy.momentum_trades = False, 2026-10-07):
    sell whatever the paper book still holds, once, in market hours. After
    that this does nothing; the dry run above keeps the rule's record."""
    held = view.momentum_held
    print(f"momentum trading is OFF (helper only); paper positions left: "
          f"{held or 'none'}")
    if not held:
        return 0
    if not (submit and view.market_open):
        print("  not selling now (market closed or preview)")
        return 0
    async with httpx.AsyncClient(timeout=60) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        open_orders = await broker._get("/v2/orders", status="open", nested="true")
        for symbol in sorted(held):
            ok = await bookorders.sell(broker, symbol, open_orders, strategy=ownership.MOMENTUM,
                                       reason="momentum stopped trading; book wound down",
                                       policy=POLICY)
            print(f"  {'sold' if ok else 'SELL FAILED'} {symbol}")
    return 0


async def main(capital: float, preview: bool, paper_mode: bool = False,
               submit: bool = False) -> int:
    today = datetime.now(tz=EXCHANGE_TZ).date()
    view = await broker_view() if paper_mode else None
    if paper_mode and not POLICY.momentum_trades:
        return await wind_down(view, submit)
    state_path, record_path = (PAPER_STATE, PAPER_RECORD) if paper_mode else (STATE, RECORD)
    if paper_mode and not state_path.exists():
        capital = view.equity * POLICY.momentum_allocation_pct / 100
    book = state.load(state_path) or bk.new_book(capital)
    idx = bk.due(book, today)
    if paper_mode:
        label = "PAPER -- SUBMITTING" if submit else "PAPER preview"
    else:
        label = "DRY RUN  (no orders are sent)"
    print(f"momentum engine {label}  {today}")
    if idx is None:
        print(f"tranche {bk.tranche_for(today)} was already rebuilt this month; nothing to do.")
        return 0
    if submit and not view.market_open:
        print("market is closed: market-on-close orders need an open session. Nothing sent.")
        return 0

    sector, bench, daily, errors = await gather(today)
    scores = {s: compute(b).momentum_12_1 for s, b in daily.items()}
    picks = signal.rank(scores)
    if paper_mode:
        dropped = [p for p in picks if p in view.foreign]
        picks = paper.drop_foreign(picks, view.foreign)
        if dropped:
            print(f"held by another strategy, left out: {', '.join(dropped)}")
    invested = signal.trend_ok(list(bench.closes))
    sma = sum(bench.closes[-signal.TREND_DAYS:]) / signal.TREND_DAYS
    needed = set(picks) | {s for t in book.tranches for s in t.holdings}
    prices = await prices_for(needed, daily, today)

    old = book.tranches[idx]
    new = bk.rebuild(old, today, picks=picks, prices=prices, invested=invested)
    after = bk.replace_tranche(book, new)
    trades = bk.orders(bk.target(book), bk.target(after))
    if paper_mode:
        trades = paper.clamp_sells(trades, view.momentum_held)

    print(f"universe {len(sector)} (S&P 500 + MidCap 400), {len(daily)} priced, "
          f"{len(errors)} failed; ranked {sum(v is not None for v in scores.values())}")
    print(f"S&P 500 {bench.closes[-1]:,.2f} vs 210-day average {sma:,.2f} -> "
          f"{'INVESTED' if invested else 'CASH (trend filter off)'}")
    print(f"\nrebuilding tranche {idx} (last formed {old.formed or 'never'}), "
          f"worth ${bk.value(old, prices):,.2f}")
    print(f"\n   {'#':>2}  {'symbol':<7}{'12-1 mom':>10}  {'sector':<26}{'shares':>8}{'price':>10}")
    for n, s in enumerate(picks, 1):
        print(f"   {n:>2}  {s:<7}{scores[s]:>+9.1f}%  {sector.get(s, '?')[:25]:<26}"
              f"{new.holdings.get(s, 0):>8}{prices[s]:>10.2f}")
    themes = Counter(sector.get(s, "?") for s in picks)
    top_theme, top_count = themes.most_common(1)[0] if themes else ("-", 0)
    print(f"\n   sector mix (logged, not enforced): "
          + ", ".join(f"{k} {v}" for k, v in themes.most_common()))
    if top_count >= 5:
        print(f"   NOTE: {top_count} of {len(picks)} picks are {top_theme} -- one theme, one bet")

    print(f"\n   trades to get there ({len(trades)}):")
    for symbol, side, qty in trades:
        print(f"     {side:<4} {qty:>6} {symbol:<6} ~${qty * prices[symbol]:>11,.2f}")
    total = sum(bk.value(t, prices) for t in after.tranches)
    print(f"\n   book after: ${total:,.2f} across 3 tranches; cash ${sum(t.cash for t in after.tranches):,.2f}")

    if preview or (paper_mode and not submit):
        print("\n--preview: nothing recorded, state unchanged.")
        return 0
    if paper_mode:
        print("\n   sending market-on-close orders:")
        failed = await submit_trades(trades, today)
        if failed:
            # State is NOT saved: the next run retries the month, and the
            # fixed client ids stop the orders that did go through from going
            # twice.
            print(f"\n{len(failed)} order(s) failed ({', '.join(failed)}); book not "
                  f"saved, the next run retries this month.")
            return 1
    record_path.parent.mkdir(parents=True, exist_ok=True)
    with record_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "date": today.isoformat(), "tranche": idx, "invested": invested,
            "sp500": bench.closes[-1], "sma210": sma, "picks": list(picks),
            "momentum": {s: scores[s] for s in picks}, "sectors": dict(themes),
            "trades": [list(t) for t in trades], "prices": {s: prices[s] for s in needed if s in prices},
            "book_value": total}) + "\n")
    state.save(after, state_path)
    print(f"\nrecorded to {record_path}; book saved to {state_path}.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--capital", type=float, default=100_000.0)
    p.add_argument("--preview", action="store_true")
    p.add_argument("--paper", action="store_true",
                   help="the paper book: sized on the momentum sleeve, real paper orders")
    p.add_argument("--submit", action="store_true",
                   help="with --paper: actually send the market-on-close orders")
    a = p.parse_args()
    if a.submit and not a.paper:
        p.error("--submit only applies with --paper")
    raise SystemExit(asyncio.run(main(a.capital, a.preview, a.paper, a.submit)))
