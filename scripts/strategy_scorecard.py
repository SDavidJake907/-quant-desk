"""One report, split by strategy: scanner vs manual picks vs momentum.

    python scripts/strategy_scorecard.py

Runs after record_outcomes.py in the 13:30 AK outcomes task (WORKLIST §37).
Closed trades come from `trade_outcome` records, grouped by client_order_id
prefix. Momentum is scored as a book: paper_book.json cash plus the market
value of the positions momentum owns, against the capital of its first
rebalance, beside the S&P 500 over the same days.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution import ownership, scorecard
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.features.bars import fetch_bars
from scanner.momentum import state

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED = "\033[32m", "\033[31m"

PAPER_STATE = Path("data/momentum/paper_book.json")
PAPER_RECORD = Path("data/momentum/paper.jsonl")


def _colour(x: float) -> str:
    return GREEN if x > 0 else RED if x < 0 else ""


def _closed_trades() -> None:
    stats = scorecard.by_strategy(scorecard.read_rows(settings.audit_log_path))
    print(f"{BOLD}closed trades by strategy{RESET}")
    if not stats:
        print(f"  {DIM}none yet{RESET}")
    for s in stats.values():
        print(f"  {s.strategy:<9} n={s.trades:<4} won {s.wins}/{s.trades} "
              f"({s.win_rate:.0%})  mean {_colour(s.mean_r)}{s.mean_r:+.2f}R{RESET}  "
              f"total {s.total_r:+.1f}R  {_colour(s.total_dollars)}"
              f"${s.total_dollars:+,.0f}{RESET}")
    small = [s.strategy for s in stats.values() if s.trades < 30]
    if small:
        print(f"  {DIM}under 30 trades ({', '.join(small)}): too few to tell skill "
              f"from luck{RESET}")


async def _momentum() -> None:
    from scanner.execution.policy import TradingPolicy
    if not TradingPolicy().momentum_trades:
        # A helper since 2026-10-07 (its picks inform both books); its old paper
        # book counted cash but not its paper positions and showed a fake loss
        # (-25.79%). Its rule is still scored, correctly, in the competition.
        print(f"\n{BOLD}momentum{RESET}: a helper for both books (not a book); "
              f"its rule is scored in the competition standings")
        return
    print(f"\n{BOLD}momentum paper book{RESET}")
    book = state.load(PAPER_STATE)
    if book is None or not PAPER_RECORD.exists():
        print(f"  {DIM}not started -- first paper rebalance not yet sent{RESET}")
        return
    first = json.loads(PAPER_RECORD.read_text(encoding="utf-8").splitlines()[0])
    start_value, start_day, start_spx = first["book_value"], first["date"], first["sp500"]
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
            positions = await broker._get("/v2/positions")
            owner_map = ownership.owners(await broker._get(
                "/v2/orders", status="all", limit=500, nested="true"))
        except BrokerError as exc:
            print(f"  broker unreadable: {exc}")
            return
        spx = await fetch_bars("^GSPC", client, range_="1mo")
    value = sum(float(p.get("market_value") or 0) for p in positions
                if ownership.owner(p["symbol"], owner_map) == ownership.MOMENTUM)
    cash = sum(t.cash for t in book.tranches)
    ret = scorecard.book_return(start_value, cash, value)
    spx_ret = (spx.closes[-1] / start_spx - 1) * 100 if spx.closes else None
    print(f"  since {start_day}: book ${cash + value:,.0f} "
          f"(positions ${value:,.0f}, cash ${cash:,.0f}) vs start ${start_value:,.0f}")
    if ret is not None:
        line = f"  return {_colour(ret)}{ret:+.2f}%{RESET}"
        if spx_ret is not None:
            line += f"   S&P 500 {spx_ret:+.2f}%   difference {ret - spx_ret:+.2f}pp"
        print(line)


async def main() -> int:
    _closed_trades()
    await _momentum()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
